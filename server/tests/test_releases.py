"""Tests for GitHub release discovery and agent update metadata."""

from unittest.mock import patch

from src.agent.releases import (
    android_release_asset_names,
    enrich_auth_with_agent_update,
    get_github_release_repo,
    linux_release_asset_names,
    release_has_assets,
    resolve_agent_update_info,
)


def test_get_github_release_repo_defaults_to_pairing_constant():
    with patch.dict('os.environ', {}, clear=True):
        assert get_github_release_repo() == 'pantherale0/timekpr-webui'


def test_get_github_release_repo_honours_env_override():
    with patch.dict('os.environ', {'TIMEKPR_GITHUB_RELEASE_REPO': 'acme/guardian-fork'}):
        assert get_github_release_repo() == 'acme/guardian-fork'


@patch('src.agent.releases.release_has_assets', return_value=True)
@patch('src.agent.releases._resolve_android_update_fields')
def test_resolve_agent_update_info_android_includes_repo(mock_fields, _mock_assets):
    mock_fields.return_value = {
        'apk_url': 'https://example.com/agent.apk',
        'signature_checksum': 'checksum-value',
    }
    info = resolve_agent_update_info('android', 'v0.68.5', server_url='wss://example.com/ws')
    assert info['update_available'] is True
    assert info['github_repo'] == 'pantherale0/timekpr-webui'
    assert info['apk_url'] == 'https://example.com/agent.apk'
    assert info['signature_checksum'] == 'checksum-value'


@patch('src.agent.releases.release_has_assets', return_value=False)
def test_resolve_agent_update_info_android_requires_release_assets(_mock_assets):
    info = resolve_agent_update_info('android', 'v0.68.5', server_url='wss://example.com/ws')
    assert info['update_available'] is False
    assert info['apk_url'] == ''


@patch('src.agent.releases.release_has_assets', return_value=True)
def test_resolve_agent_update_info_linux_uses_arch_specific_assets(_mock_assets):
    info = resolve_agent_update_info(
        'linux',
        'v0.68.5',
        agent_arch='aarch64',
    )
    primary, checksum = linux_release_asset_names('aarch64-unknown-linux-gnu')
    assert info['update_available'] is True
    assert primary in info['download_url']
    assert checksum in info['checksum_url']


@patch('src.agent.releases.resolve_agent_update_info')
def test_enrich_auth_with_agent_update_mandatory_sets_required(mock_resolve):
    mock_resolve.return_value = {
        'github_repo': 'pantherale0/timekpr-webui',
        'target_version': 'v0.68.5',
        'update_available': True,
        'apk_url': 'https://example.com/agent.apk',
        'signature_checksum': 'checksum',
        'download_url': '',
        'checksum_url': '',
    }
    payload = enrich_auth_with_agent_update(
        {'type': 'auth_result', 'success': False, 'message': 'update'},
        platform='android',
        server_version='v0.68.5',
        agent_version='v0.67.0',
        server_url='wss://example.com/ws',
        mandatory=True,
    )
    assert payload['update_required'] is True
    assert payload['update_available'] is True
    assert payload['github_repo'] == 'pantherale0/timekpr-webui'
    assert payload['apk_url'] == 'https://example.com/agent.apk'


@patch('src.agent.releases.resolve_agent_update_info')
def test_enrich_auth_with_agent_update_skips_hint_without_assets(mock_resolve):
    mock_resolve.return_value = {
        'github_repo': 'pantherale0/timekpr-webui',
        'target_version': 'v0.68.5',
        'update_available': False,
        'apk_url': '',
        'signature_checksum': '',
        'download_url': '',
        'checksum_url': '',
    }
    payload = enrich_auth_with_agent_update(
        {'type': 'auth_result', 'success': True, 'message': 'ok'},
        platform='linux',
        server_version='v0.68.5',
        agent_version='v0.68.0',
        server_url='wss://example.com/ws',
        mandatory=False,
    )
    assert payload['update_available'] is False
    assert 'download_url' not in payload or payload['download_url'] == ''


def test_android_release_asset_names_use_tag_suffix():
    assert android_release_asset_names('v0.68.5') == (
        'guardian-android-agent-v0.68.5.apk',
        'guardian-android-agent-v0.68.5.signature-checksum',
    )


@patch('src.agent.releases._fetch_github_release_asset_names')
def test_release_has_assets_checks_required_names(mock_fetch):
    mock_fetch.return_value = frozenset(
        {
            'guardian-android-agent-v0.68.5.apk',
            'guardian-android-agent-v0.68.5.signature-checksum',
        }
    )
    primary, checksum = android_release_asset_names('v0.68.5')
    assert release_has_assets('pantherale0/timekpr-webui', 'v0.68.5', (primary, checksum)) is True
