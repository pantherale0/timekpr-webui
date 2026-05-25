import hashlib
import json
import re
from datetime import datetime, timedelta
from urllib.parse import urlparse


DOMAIN_LABEL_RE = re.compile(r'^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$')
EXTERNAL_SYNC_INTERVAL = timedelta(hours=24)


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


def parse_blocklist_text(raw_text, strict=False):
    domains = []
    errors = []
    seen = set()

    for line_number, raw_line in enumerate((raw_text or '').splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue

        try:
            domain = normalize_domain(line)
        except ValueError as exc:
            if strict:
                raise ValueError(f'Line {line_number}: {exc}') from exc
            errors.append(f'Line {line_number}: {exc}')
            continue

        if domain not in seen:
            seen.add(domain)
            domains.append(domain)

    return domains, errors


def validate_external_source_url(raw_url):
    normalized = (raw_url or '').strip()
    if not normalized:
        raise ValueError('External blocklist URL is required')

    parsed = urlparse(normalized)
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
        raise ValueError('External blocklist URL must use http:// or https://')

    return normalized


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


def compute_mapping_policy_hash(linux_uid, source_domain_map, assigned_source_ids):
    payload = {
        'linux_uid': linux_uid,
        'sources': {
            str(source_id): source_domain_map.get(str(source_id), [])
            for source_id in sorted({int(source_id) for source_id in assigned_source_ids})
        },
    }
    digest_source = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(digest_source.encode('utf-8')).hexdigest()


def summarize_mapping_blocklist_sync(mapping, source_domain_map, assigned_source_ids):
    source_ids = sorted({int(source_id) for source_id in assigned_source_ids})
    effective_domain_count = sum(
        len(source_domain_map.get(str(source_id), []))
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

    policy_hash = compute_mapping_policy_hash(mapping.linux_uid, source_domain_map, source_ids)
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
