"""Firebase Cloud Messaging helpers for Android agents."""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import requests

_LOGGER = logging.getLogger(__name__)

FCM_LEGACY_ENDPOINT = 'https://fcm.googleapis.com/fcm/send'
FCM_V1_SCOPE = 'https://www.googleapis.com/auth/firebase.messaging'
FCM_ACTION_SYNC_POLICIES = 'sync_policies'
FCM_ACTION_PAIRING_APPROVED = 'pairing_approved'
FCM_ACTION_CONNECT = 'connect'
FCM_ACTION_COMMAND_WAKE = 'command_wake'

_cached_access_token: str | None = None
_cached_access_token_expires_at = 0.0


def is_fcm_configured() -> bool:
    return bool((os.environ.get('FCM_SERVER_KEY') or '').strip()) or bool(
        (os.environ.get('FIREBASE_CREDENTIALS_JSON') or '').strip()
    )


def _load_service_account() -> dict | None:
    raw = (os.environ.get('FIREBASE_CREDENTIALS_JSON') or '').strip()
    if not raw:
        return None
    if raw.startswith('{'):
        return json.loads(raw)
    with open(raw, encoding='utf-8') as handle:
        return json.load(handle)


def _get_access_token() -> str | None:
    global _cached_access_token, _cached_access_token_expires_at

    if _cached_access_token and time.time() < _cached_access_token_expires_at - 60:
        return _cached_access_token

    service_account = _load_service_account()
    if not service_account:
        return None

    try:
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account
    except ImportError:
        _LOGGER.warning('google-auth is not installed; cannot use Firebase HTTP v1 API')
        return None

    credentials = service_account.Credentials.from_service_account_info(
        service_account,
        scopes=[FCM_V1_SCOPE],
    )
    credentials.refresh(Request())
    _cached_access_token = credentials.token
    _cached_access_token_expires_at = time.time() + 3300
    return _cached_access_token


def _send_legacy(token: str, data: dict[str, str], priority: str = 'high') -> tuple[bool, str]:
    server_key = (os.environ.get('FCM_SERVER_KEY') or '').strip()
    if not server_key:
        return False, 'FCM_SERVER_KEY is not configured'

    payload = {
        'to': token,
        'priority': priority,
        'data': data,
    }
    response = requests.post(
        FCM_LEGACY_ENDPOINT,
        headers={
            'Authorization': f'key={server_key}',
            'Content-Type': 'application/json',
        },
        json=payload,
        timeout=15,
    )
    if response.status_code >= 400:
        return False, f'FCM legacy API error {response.status_code}: {response.text[:200]}'

    body = response.json()
    if body.get('failure', 0):
        return False, f'FCM delivery failed: {body}'
    return True, 'FCM message accepted'


def _send_http_v1(token: str, data: dict[str, str], priority: str = 'HIGH') -> tuple[bool, str]:
    service_account = _load_service_account()
    if not service_account:
        return False, 'FIREBASE_CREDENTIALS_JSON is not configured'

    project_id = service_account.get('project_id') or os.environ.get('FIREBASE_PROJECT_ID')
    if not project_id:
        return False, 'Firebase project_id is missing'

    access_token = _get_access_token()
    if not access_token:
        return False, 'Unable to obtain Firebase access token'

    message = {
        'message': {
            'token': token,
            'data': data,
            'android': {
                'priority': priority,
            },
        },
    }
    url = f'https://fcm.googleapis.com/v1/projects/{project_id}/messages:send'
    response = requests.post(
        url,
        headers={
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json',
        },
        json=message,
        timeout=15,
    )
    if response.status_code >= 400:
        return False, f'FCM v1 API error {response.status_code}: {response.text[:200]}'
    return True, 'FCM message accepted'


def send_data_message(token: str, data: dict[str, Any], priority: str = 'high') -> tuple[bool, str]:
    """Send a data-only FCM message to a single device token."""
    normalized_token = (token or '').strip()
    if not normalized_token:
        return False, 'Missing FCM token'

    if not is_fcm_configured():
        return False, 'FCM is not configured on the server'

    string_data = {str(key): str(value) for key, value in data.items() if value is not None}

    if _load_service_account():
        android_priority = 'HIGH' if priority == 'high' else 'NORMAL'
        return _send_http_v1(normalized_token, string_data, priority=android_priority)

    return _send_legacy(normalized_token, string_data, priority=priority)


def notify_android_agent(
    device,
    action: str,
    *,
    reason: str | None = None,
    secure_token: str | None = None,
) -> tuple[bool, str]:
    """Push a wake/action message to an Android agent via FCM."""
    token = (device.fcm_token or '').strip() if device else ''
    if not token:
        return False, 'Device has no FCM token'

    payload = {
        'action': action,
        'system_id': device.system_id,
    }
    if reason:
        payload['reason'] = reason
    if secure_token:
        payload['token'] = secure_token

    return send_data_message(token, payload, priority='high')
