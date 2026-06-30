"""GitHub release discovery and agent update metadata resolution."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import requests

from src.agent.pairing import (
    GITHUB_RELEASE_REPO,
    build_uploaded_android_apk_url,
    get_android_apk_storage_path,
    has_uploaded_android_apk,
    is_dev_server_version,
    resolve_android_apk_url,
    resolve_android_signature_checksum,
)

_LOGGER = logging.getLogger(__name__)

_RELEASE_CACHE: dict[str, tuple[frozenset[str], float]] = {}
_RELEASE_CACHE_TTL_SECONDS = int(os.environ.get('TIMEKPR_RELEASE_CACHE_TTL_SECONDS', '120'))


def get_github_release_repo() -> str:
    """Return the GitHub owner/repo agents should use for release assets."""
    explicit = (os.environ.get('TIMEKPR_GITHUB_RELEASE_REPO') or '').strip()
    if explicit:
        return explicit
    return GITHUB_RELEASE_REPO


def normalize_release_tag(version: str) -> str:
    tag = (version or '').strip()
    if not tag:
        return ''
    return tag if tag.startswith('v') else f'v{tag}'


def normalize_platform(platform: str | None) -> str:
    return (platform or '').strip().lower()


def build_github_download_url(repo: str, tag: str, asset_name: str) -> str:
    return f'https://github.com/{repo}/releases/download/{tag}/{asset_name}'


def android_release_asset_names(tag: str) -> tuple[str, str]:
    return (
        f'guardian-android-agent-{tag}.apk',
        f'guardian-android-agent-{tag}.signature-checksum',
    )


def linux_release_target(agent_arch: str | None) -> str | None:
    arch = (agent_arch or '').strip().lower()
    if arch == 'x86_64':
        return 'x86_64-unknown-linux-gnu'
    if arch == 'aarch64':
        return 'aarch64-unknown-linux-gnu'
    return None


def linux_release_asset_names(target_triple: str) -> tuple[str, str]:
    archive = f'guardian-agent-{target_triple}.tar.gz'
    return archive, f'{archive}.sha256'


def windows_release_asset_names() -> tuple[str, str]:
    installer = 'guardian-agent-x86_64-pc-windows-msvc.msi'
    return installer, f'{installer}.sha256'


def _fetch_github_release_asset_names(repo: str, tag: str) -> frozenset[str] | None:
    """Return release asset names, caching results for a short TTL."""
    cache_key = f'{repo}:{tag}'
    now = time.monotonic()
    cached = _RELEASE_CACHE.get(cache_key)
    if cached and (now - cached[1]) < _RELEASE_CACHE_TTL_SECONDS:
        return cached[0]

    api_url = f'https://api.github.com/repos/{repo}/releases/tags/{tag}'
    try:
        response = requests.get(
            api_url,
            timeout=10,
            headers={'Accept': 'application/vnd.github+json'},
        )
        if response.status_code == 404:
            names: frozenset[str] = frozenset()
        elif response.status_code != 200:
            _LOGGER.debug(
                'GitHub release lookup for %s@%s returned HTTP %s',
                repo,
                tag,
                response.status_code,
            )
            return None
        else:
            payload = response.json()
            names = frozenset(
                asset.get('name', '')
                for asset in payload.get('assets', [])
                if asset.get('name')
            )
        _RELEASE_CACHE[cache_key] = (names, now)
        return names
    except requests.RequestException as exc:
        _LOGGER.debug('Failed to fetch GitHub release assets for %s@%s: %s', repo, tag, exc)
        return None


def release_has_assets(repo: str, tag: str, required_assets: tuple[str, ...]) -> bool:
    """Return True when every named asset exists on the cached GitHub release."""
    if not repo or not tag or not required_assets:
        return False
    names = _fetch_github_release_asset_names(repo, tag)
    if names is None:
        return False
    return all(asset_name in names for asset_name in required_assets)


def _empty_update_payload(target_version: str) -> dict[str, Any]:
    return {
        'github_repo': get_github_release_repo(),
        'target_version': normalize_release_tag(target_version) or target_version,
        'update_available': False,
        'apk_url': '',
        'signature_checksum': '',
        'download_url': '',
        'checksum_url': '',
    }


def _resolve_android_update_fields(tag: str, server_url: str) -> dict[str, str]:
    apk_url = resolve_android_apk_url(tag, server_url=server_url)
    signature_checksum = resolve_android_signature_checksum(tag) or ''
    return {
        'apk_url': apk_url or '',
        'signature_checksum': signature_checksum,
    }


def resolve_agent_update_info(
    platform: str | None,
    target_version: str,
    server_url: str = '',
    agent_arch: str | None = None,
) -> dict[str, Any]:
    """Resolve optional/mandatory agent update metadata for a platform."""
    payload = _empty_update_payload(target_version)
    normalized_platform = normalize_platform(platform)
    tag = normalize_release_tag(target_version)
    if not tag:
        return payload

    if is_dev_server_version(target_version):
        if normalized_platform == 'android' and has_uploaded_android_apk() and (server_url or '').strip():
            fields = _resolve_android_update_fields(tag, server_url)
            if fields['apk_url'] and fields['signature_checksum']:
                payload.update(fields)
                payload['update_available'] = True
        return payload

    repo = get_github_release_repo()
    payload['github_repo'] = repo

    if normalized_platform == 'android':
        primary_asset, checksum_asset = android_release_asset_names(tag)
        if not release_has_assets(repo, tag, (primary_asset, checksum_asset)):
            return payload
        fields = _resolve_android_update_fields(tag, server_url)
        if fields['apk_url'] and fields['signature_checksum']:
            payload.update(fields)
            payload['update_available'] = True
        return payload

    if normalized_platform == 'linux':
        target_triple = linux_release_target(agent_arch)
        if not target_triple:
            return payload
        primary_asset, checksum_asset = linux_release_asset_names(target_triple)
        if not release_has_assets(repo, tag, (primary_asset, checksum_asset)):
            return payload
        payload['download_url'] = build_github_download_url(repo, tag, primary_asset)
        payload['checksum_url'] = build_github_download_url(repo, tag, checksum_asset)
        payload['update_available'] = True
        return payload

    if normalized_platform == 'windows':
        primary_asset, checksum_asset = windows_release_asset_names()
        if not release_has_assets(repo, tag, (primary_asset, checksum_asset)):
            return payload
        payload['download_url'] = build_github_download_url(repo, tag, primary_asset)
        payload['checksum_url'] = build_github_download_url(repo, tag, checksum_asset)
        payload['update_available'] = True
        return payload

    return payload


def resolve_android_update_info(version: str, server_url: str = '') -> dict[str, Any]:
    """Backward-compatible Android update metadata helper."""
    info = resolve_agent_update_info('android', version, server_url=server_url)
    return {
        'github_repo': info.get('github_repo', ''),
        'apk_url': info.get('apk_url', ''),
        'signature_checksum': info.get('signature_checksum', ''),
        'update_available': bool(info.get('update_available')),
    }


def enrich_auth_with_agent_update(
    auth_payload: dict[str, Any],
    *,
    platform: str | None,
    server_version: str,
    agent_version: str | None,
    server_url: str,
    agent_arch: str | None = None,
    mandatory: bool = False,
) -> dict[str, Any]:
    """Attach update metadata to an auth_result when a release asset is available."""
    auth_payload.setdefault('target_version', server_version)
    auth_payload.setdefault('github_repo', get_github_release_repo())
    auth_payload['update_available'] = False

    if mandatory:
        auth_payload['update_required'] = True

    update_info = resolve_agent_update_info(
        platform,
        server_version,
        server_url=server_url,
        agent_arch=agent_arch,
    )
    if not update_info.get('update_available'):
        return auth_payload

    auth_payload.update(update_info)
    return auth_payload
