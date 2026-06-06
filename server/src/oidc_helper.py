"""Helpers for OpenID Connect discovery and login flows."""

import logging
import os
import secrets

import requests

logger = logging.getLogger(__name__)


class OIDCHelper:
    """Fetch and cache OIDC metadata, then drive browser-based login."""

    def __init__(self):
        """Load OIDC settings from the environment."""
        self.issuer_url = os.environ.get('OIDC_ISSUER_URL')
        self.client_id = os.environ.get('OIDC_CLIENT_ID')
        self.client_secret = os.environ.get('OIDC_CLIENT_SECRET')
        self.redirect_uri_override = os.environ.get('OIDC_REDIRECT_URI')

        # Load verify SSL toggle (default to True)
        verify_ssl_str = os.environ.get('OIDC_VERIFY_SSL', 'true').lower()
        self.verify_ssl = verify_ssl_str not in ('false', '0', 'no', 'off')

        self._endpoints = None

    @property
    def is_enabled(self):
        """Returns True if OIDC is configured with all required parameters."""
        return bool(self.issuer_url and self.client_id and self.client_secret)

    def _fetch_discovery(self):
        """Fetches OIDC discovery document and caches it."""
        if self._endpoints is not None:
            return self._endpoints

        if not self.issuer_url:
            raise ValueError("OIDC Issuer URL is not configured.")

        discovery_url = f"{self.issuer_url.rstrip('/')}/.well-known/openid-configuration"
        try:
            logger.info("Fetching OIDC configuration from: %s", discovery_url)
            response = requests.get(discovery_url, verify=self.verify_ssl, timeout=10)
            response.raise_for_status()
            self._endpoints = response.json()
            return self._endpoints
        except (requests.RequestException, ValueError) as exc:
            logger.error(
                "Failed to fetch OIDC discovery document from %s: %s",
                discovery_url,
                exc,
            )
            raise RuntimeError(f"OIDC configuration error: {exc}") from exc

    def get_authorization_url(self, state, redirect_uri):
        """Constructs the authorization URL to redirect the user to."""
        endpoints = self._fetch_discovery()
        auth_endpoint = endpoints.get('authorization_endpoint')
        if not auth_endpoint:
            raise KeyError("OIDC discovery is missing 'authorization_endpoint'")

        # Use redirect URI override if configured, otherwise use dynamic redirect_uri
        actual_redirect = self.redirect_uri_override or redirect_uri

        params = {
            'response_type': 'code',
            'client_id': self.client_id,
            'redirect_uri': actual_redirect,
            'scope': 'openid profile email',
            'state': state
        }

        # Construct full URL with query parameters
        req = requests.models.PreparedRequest()
        req.prepare_url(auth_endpoint, params)
        return req.url

    def exchange_code(self, code, redirect_uri):
        """Exchanges authorization code for access and ID tokens."""
        endpoints = self._fetch_discovery()
        token_endpoint = endpoints.get('token_endpoint')
        if not token_endpoint:
            raise KeyError("OIDC discovery is missing 'token_endpoint'")

        actual_redirect = self.redirect_uri_override or redirect_uri

        data = {
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': actual_redirect,
            'client_id': self.client_id,
            'client_secret': self.client_secret,
        }

        try:
            logger.info("Exchanging code at OIDC token endpoint: %s", token_endpoint)
            response = requests.post(
                token_endpoint,
                data=data,
                verify=self.verify_ssl,
                timeout=10,
            )
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            logger.error("Failed to exchange OIDC authorization code: %s", exc)
            raise RuntimeError(f"OIDC code exchange failed: {exc}") from exc

    def get_user_info(self, access_token):
        """Retrieves user profile using access token."""
        endpoints = self._fetch_discovery()
        userinfo_endpoint = endpoints.get('userinfo_endpoint')
        if not userinfo_endpoint:
            raise KeyError("OIDC discovery is missing 'userinfo_endpoint'")

        headers = {
            'Authorization': f'Bearer {access_token}',
        }

        try:
            logger.info("Fetching userinfo from: %s", userinfo_endpoint)
            response = requests.get(
                userinfo_endpoint,
                headers=headers,
                verify=self.verify_ssl,
                timeout=10,
            )
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            logger.error("Failed to fetch OIDC user info: %s", exc)
            raise RuntimeError(f"OIDC user info retrieval failed: {exc}") from exc

    @staticmethod
    def generate_state():
        """Generates a secure cryptographically random state for CSRF protection."""
        return secrets.token_urlsafe(16)

    @staticmethod
    def _parse_csv_env(name):
        value = os.environ.get(name, '')
        return [item.strip().lower() for item in value.split(',') if item.strip()]

    @staticmethod
    def _normalize_claim_values(raw_value):
        if raw_value is None:
            return []
        if isinstance(raw_value, str):
            values = [raw_value]
        elif isinstance(raw_value, (list, tuple, set)):
            values = list(raw_value)
        else:
            values = [str(raw_value)]
        return [str(value).strip().lower() for value in values if str(value).strip()]

    def is_authorized_admin(self, user_info):
        """Return whether the OIDC identity is allowed to access the admin console."""
        if not user_info:
            return False, 'Missing user information from identity provider'

        allow_any = os.environ.get('OIDC_ALLOW_ANY_AUTHENTICATED', '').strip().lower()
        if allow_any in {'true', '1', 'yes', 'on'}:
            logger.warning(
                'OIDC_ALLOW_ANY_AUTHENTICATED is enabled; any authenticated identity may access the admin console'
            )
            return True, ''

        allowed_emails = self._parse_csv_env('ALLOWED_OIDC_ADMINS')
        allowed_domains = self._parse_csv_env('ALLOWED_OIDC_ADMIN_DOMAINS')
        allowed_roles = self._parse_csv_env('ALLOWED_OIDC_ADMIN_ROLES')
        allowed_groups = self._parse_csv_env('ALLOWED_OIDC_ADMIN_GROUPS')

        if not any((allowed_emails, allowed_domains, allowed_roles, allowed_groups)):
            return False, (
                'OIDC admin access is not configured. Set ALLOWED_OIDC_ADMINS, '
                'ALLOWED_OIDC_ADMIN_DOMAINS, ALLOWED_OIDC_ADMIN_ROLES, or ALLOWED_OIDC_ADMIN_GROUPS.'
            )

        email = (user_info.get('email') or '').strip().lower()
        if allowed_emails and email in allowed_emails:
            return True, ''
        if allowed_domains and '@' in email:
            domain = email.rsplit('@', 1)[-1]
            if domain in allowed_domains:
                return True, ''

        role_values = self._normalize_claim_values(
            user_info.get('roles') or user_info.get('role')
        )
        if allowed_roles and any(role in allowed_roles for role in role_values):
            return True, ''

        group_values = self._normalize_claim_values(
            user_info.get('groups') or user_info.get('group')
        )
        if allowed_groups and any(group in allowed_groups for group in group_values):
            return True, ''

        return False, 'You are not authorized to access the admin console.'
