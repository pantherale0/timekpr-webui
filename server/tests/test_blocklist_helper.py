import pytest

from src.blocklist_helper import BlocklistStreamParser


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
