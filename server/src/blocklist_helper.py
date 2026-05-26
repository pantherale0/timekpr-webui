import codecs
import hashlib
import json
import re
from datetime import datetime, timedelta
from urllib.parse import urlparse

from sqlalchemy import func

from src.database import BlocklistDomain


DOMAIN_LABEL_RE = re.compile(r'^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$')
EXTERNAL_SYNC_INTERVAL = timedelta(hours=24)
BLOCKLIST_STREAM_CHUNK_SIZE = 64 * 1024
BLOCKLIST_SYNC_BATCH_SIZE = 1000
MAX_BLOCKLIST_LINE_LENGTH = 4096
MAX_BLOCKLIST_ERRORS = 20


def normalize_domain(raw_domain):
    if raw_domain is None:
        raise ValueError('Domain is required')

    domain = str(raw_domain).strip().lower().rstrip('.')
    if not domain:
        raise ValueError('Domain must not be empty')
    if domain.startswith('#'):
        raise ValueError('Comment lines are not valid domains')
    if '://' in domain:
        raise ValueError('URLs are not valid domains')
    if domain.startswith('*.'):
        raise ValueError('Wildcard domains are not supported')
    if any(char.isspace() for char in domain):
        raise ValueError('Domain must not contain whitespace')
    if '/' in domain or ':' in domain:
        raise ValueError('Domain must not contain URL or port separators')
    if domain.count('.') < 1:
        raise ValueError('Domain must contain at least one dot')

    labels = domain.split('.')
    if any(not label for label in labels):
        raise ValueError('Domain contains an empty label')
    for label in labels:
        if len(label) > 63 or not DOMAIN_LABEL_RE.match(label):
            raise ValueError(f'Invalid domain label: {label}')

    return domain


def _parse_blocklist_line(raw_line, line_number):
    line = str(raw_line or '').strip()
    if not line or line.startswith('#'):
        return None, None

    try:
        return normalize_domain(line), None
    except ValueError as exc:
        return None, f'Line {line_number}: {exc}'


def parse_blocklist_text(raw_text, strict=False):
    domains = []
    errors = []
    seen = set()

    for line_number, raw_line in enumerate((raw_text or '').splitlines(), start=1):
        domain, error = _parse_blocklist_line(raw_line, line_number)
        if error:
            if strict:
                raise ValueError(error)
            errors.append(error)
            continue
        if domain is None:
            continue

        if domain not in seen:
            seen.add(domain)
            domains.append(domain)

    return domains, errors


class BlocklistStreamParser:
    def __init__(
        self,
        *,
        strict=False,
        error_limit=MAX_BLOCKLIST_ERRORS,
        line_length_limit=MAX_BLOCKLIST_LINE_LENGTH,
    ):
        self.strict = strict
        self.error_limit = error_limit
        self.line_length_limit = line_length_limit
        self.errors = []
        self.ignored_error_count = 0

    def _record_error(self, message):
        if self.strict:
            raise ValueError(message)
        if len(self.errors) < self.error_limit:
            self.errors.append(message)
        else:
            self.ignored_error_count += 1

    def _check_pending_length(self, pending_line, line_number):
        if len(pending_line) <= self.line_length_limit:
            return
        raise ValueError(
            f'Line {line_number} exceeds maximum length of {self.line_length_limit} characters'
        )

    def _handle_line(self, raw_line, line_number, batch, batch_size):
        domain, error = _parse_blocklist_line(raw_line, line_number)
        if error:
            self._record_error(error)
            return None
        if domain is None:
            return None

        batch.append(domain)
        if len(batch) >= batch_size:
            flushed_batch = list(batch)
            batch.clear()
            return flushed_batch
        return None

    def iter_domain_batches(
        self,
        byte_chunks,
        *,
        encoding='utf-8',
        batch_size=BLOCKLIST_SYNC_BATCH_SIZE,
    ):
        decoder = codecs.getincrementaldecoder(encoding or 'utf-8')(errors='replace')
        pending = ''
        line_number = 0
        batch = []

        def drain_pending(decoded_text, final=False):
            nonlocal pending, line_number
            pending += decoded_text

            while True:
                newline_index = pending.find('\n')
                if newline_index < 0:
                    break

                raw_line = pending[:newline_index]
                if raw_line.endswith('\r'):
                    raw_line = raw_line[:-1]
                pending = pending[newline_index + 1:]
                line_number += 1

                flushed_batch = self._handle_line(raw_line, line_number, batch, batch_size)
                if flushed_batch is not None:
                    yield flushed_batch

            if pending:
                self._check_pending_length(pending, line_number + 1)

            if final and pending:
                final_line = pending[:-1] if pending.endswith('\r') else pending
                line_number += 1
                flushed_batch = self._handle_line(final_line, line_number, batch, batch_size)
                pending = ''
                if flushed_batch is not None:
                    yield flushed_batch

        for raw_chunk in byte_chunks:
            if not raw_chunk:
                continue
            decoded_chunk = decoder.decode(raw_chunk)
            yield from drain_pending(decoded_chunk)

        yield from drain_pending(decoder.decode(b'', final=True), final=True)

        if batch:
            yield list(batch)
            batch.clear()

    def collected_errors(self):
        if self.ignored_error_count:
            return self.errors + [
                f'{self.ignored_error_count} additional parse error(s) omitted'
            ]
        return list(self.errors)


def validate_external_source_url(raw_url):
    normalized = (raw_url or '').strip()
    if not normalized:
        raise ValueError('External blocklist URL is required')

    parsed = urlparse(normalized)
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
        raise ValueError('External blocklist URL must use http:// or https://')

    return normalized


def compute_source_revision(domains):
    digest = hashlib.sha256()
    for domain in sorted({
        str(domain).strip().lower().rstrip('.')
        for domain in (domains or [])
        if str(domain).strip()
    }):
        digest.update(domain.encode('utf-8'))
        digest.update(b'\n')
    return digest.hexdigest()


def compute_source_revision_for_source_id(source_id):
    digest = hashlib.sha256()
    query = (
        BlocklistDomain.query.with_entities(BlocklistDomain.domain)
        .filter_by(source_id=source_id)
        .order_by(BlocklistDomain.domain.asc())
        .yield_per(BLOCKLIST_SYNC_BATCH_SIZE)
    )
    for domain, in query:
        if not domain:
            continue
        digest.update(domain.encode('utf-8'))
        digest.update(b'\n')
    return digest.hexdigest()


def iter_source_domain_batches(source_id, batch_size=BLOCKLIST_SYNC_BATCH_SIZE):
    batch = []
    query = (
        BlocklistDomain.query.with_entities(BlocklistDomain.domain)
        .filter_by(source_id=source_id)
        .order_by(BlocklistDomain.domain.asc())
        .yield_per(batch_size)
    )
    for domain, in query:
        if not domain:
            continue
        batch.append(domain)
        if len(batch) >= batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


def build_source_state_map(sources):
    source_rows = [
        source
        for source in (sources or [])
        if getattr(source, 'is_enabled', True)
    ]
    source_ids = [source.id for source in source_rows]
    domain_count_map = {}

    if source_ids:
        count_rows = (
            BlocklistDomain.query.with_entities(
                BlocklistDomain.source_id,
                func.count(BlocklistDomain.id),
            )
            .filter(BlocklistDomain.source_id.in_(source_ids))
            .group_by(BlocklistDomain.source_id)
            .all()
        )
        domain_count_map = {
            int(source_id): int(domain_count)
            for source_id, domain_count in count_rows
        }

    return {
        str(source.id): {
            'revision': (
                getattr(source, 'content_revision', None)
                or compute_source_revision_for_source_id(source.id)
            ),
            'domain_count': domain_count_map.get(source.id, 0),
        }
        for source in source_rows
    }


def build_source_domain_map(sources):
    domain_map = {}
    for source in sources:
        if not getattr(source, 'is_enabled', True):
            continue

        source_domains = sorted({
            domain.domain
            for domain in getattr(source, 'domains', [])
            if getattr(domain, 'domain', None)
        })
        if source_domains:
            domain_map[str(source.id)] = source_domains
    return domain_map


def compute_mapping_policy_hash(linux_uid, source_state_map, assigned_source_ids):
    payload = {
        'linux_uid': linux_uid,
        'sources': {
            str(source_id): (
                source_state_map.get(str(source_id), {}).get('revision')
                or ''
            )
            for source_id in sorted({int(source_id) for source_id in assigned_source_ids})
        },
    }
    digest_source = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(digest_source.encode('utf-8')).hexdigest()


def summarize_mapping_blocklist_sync(mapping, source_state_map, assigned_source_ids):
    source_ids = sorted({int(source_id) for source_id in assigned_source_ids})
    effective_domain_count = sum(
        int(source_state_map.get(str(source_id), {}).get('domain_count') or 0)
        for source_id in source_ids
    )

    if not source_ids:
        return {
            'needs_sync': bool(mapping.blocklist_policy_hash),
            'status': 'pending_clear' if mapping.blocklist_policy_hash else 'not_configured',
            'effective_domain_count': 0,
            'policy_hash': None,
        }

    if mapping.linux_uid is None:
        return {
            'needs_sync': True,
            'status': 'awaiting_uid',
            'effective_domain_count': effective_domain_count,
            'policy_hash': None,
        }

    policy_hash = compute_mapping_policy_hash(mapping.linux_uid, source_state_map, source_ids)
    is_current = (
        bool(mapping.blocklist_is_synced)
        and mapping.blocklist_policy_hash == policy_hash
    )
    return {
        'needs_sync': not is_current,
        'status': 'synced' if is_current else 'pending',
        'effective_domain_count': effective_domain_count,
        'policy_hash': policy_hash,
    }


def should_refresh_external_source(source, now=None):
    if source is None:
        return False

    if getattr(source, 'source_type', None) != getattr(source, 'TYPE_EXTERNAL_URL', 'external_url'):
        return False

    if not getattr(source, 'is_enabled', True):
        return False

    if not getattr(source, 'source_url', None):
        return False

    if getattr(source, 'last_sync_at', None) is None:
        return True

    reference_time = now or datetime.utcnow()
    return (reference_time - source.last_sync_at) >= EXTERNAL_SYNC_INTERVAL
