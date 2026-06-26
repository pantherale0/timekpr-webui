from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.blocklist.helper import (
    EXTERNAL_SYNC_INTERVAL,
    BlocklistStreamParser,
    should_refresh_external_source,
)


def test_stream_parser_reads_domains_in_batches():
    parser = BlocklistStreamParser()
    chunks = [
        b'dns.go',
        b'ogle\n# comment\ncloud',
        b'flare-dns.com\ninvalid entry\nexample.com\n',
    ]

    batches = list(parser.iter_domain_batches(chunks, batch_size=2))

    assert batches == [
        ['dns.google', 'cloudflare-dns.com'],
        ['example.com'],
    ]
    assert parser.collected_errors() == [
        'Line 4: Domain must not contain whitespace',
    ]


def test_stream_parser_rejects_overlong_lines():
    parser = BlocklistStreamParser(line_length_limit=32)

    with pytest.raises(ValueError, match='exceeds maximum length'):
        list(parser.iter_domain_batches([b'a' * 64]))


def test_should_refresh_external_source_handles_naive_last_sync_at():
    now = datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc)
    source = SimpleNamespace(
        source_type='external_url',
        TYPE_EXTERNAL_URL='external_url',
        is_enabled=True,
        source_url='https://example.com/list.txt',
        last_sync_at=datetime(2026, 6, 4, 11, 0),
    )

    assert should_refresh_external_source(source, now=now) is True

    source.last_sync_at = now - (EXTERNAL_SYNC_INTERVAL - timedelta(minutes=1))
    assert should_refresh_external_source(source, now=now) is False
