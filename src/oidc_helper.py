import os
import logging
import secrets
import requests

logger = logging.getLogger(__name__)

class OIDCHelper:
    def __init__(self):
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
        if self._endpoints:
            return self._endpoints

        if not self.issuer_url:
            raise ValueError("OIDC Issuer URL is not configured.")

        discovery_url = f"{self.issuer_url.rstrip('/')}/.well-known/openid-configuration"
        try:
            logger.info(f"Fetching OIDC configuration from: {discovery_url}")
            response = requests.get(discovery_url, verify=self.verify_ssl, timeout=10)
            response.raise_for_status()
            self._endpoints = response.json()
            return self._endpoints
        except Exception as e:
            logger.error(f"Failed to fetch OIDC discovery document from {discovery_url}: {e}")
            raise RuntimeError(f"OIDC configuration error: {str(e)}")

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
            'client_secret': self.client_secret
        }

        try:
            logger.info(f"Exchanging code at OIDC token endpoint: {token_endpoint}")
            response = requests.post(token_endpoint, data=data, verify=self.verify_ssl, timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Failed to exchange OIDC authorization code: {e}")
            raise RuntimeError(f"OIDC code exchange failed: {str(e)}")

    def get_user_info(self, access_token):
        """Retrieves user profile using access token."""
        endpoints = self._fetch_discovery()
        userinfo_endpoint = endpoints.get('userinfo_endpoint')
        if not userinfo_endpoint:
            raise KeyError("OIDC discovery is missing 'userinfo_endpoint'")

        headers = {
            'Authorization': f'Bearer {access_token}'
        }

        try:
            logger.info(f"Fetching userinfo from: {userinfo_endpoint}")
            response = requests.get(userinfo_endpoint, headers=headers, verify=self.verify_ssl, timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Failed to fetch OIDC user info: {e}")
            raise RuntimeError(f"OIDC user info retrieval failed: {str(e)}")

    @staticmethod
    def generate_state():
        """Generates a secure cryptographically random state for CSRF protection."""
        return secrets.token_urlsafe(16)
