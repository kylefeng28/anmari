"""
Custom Azure credential that exchanges OAuth authorization code for access token using MSAL.
"""

import time
from typing import Optional
from azure.core.credentials import AccessToken
from msal import ConfidentialClientApplication, PublicClientApplication


class AuthCodeCredential:
    """Custom credential that exchanges auth code for access token using MSAL"""

    def __init__(self, client_id: str, tenant_id: str, redirect_uri: str, 
                 auth_code: Optional[str] = None, client_secret: Optional[str] = None,
                 scopes: Optional[list] = None):
        self.client_id = client_id
        self.tenant_id = tenant_id
        self.redirect_uri = redirect_uri
        self.auth_code = auth_code
        self.scopes = scopes or ["https://graph.microsoft.com/.default"]

        authority = f"https://login.microsoftonline.com/{tenant_id}"

        if client_secret:
            self.app = ConfidentialClientApplication(
                client_id, authority=authority, client_credential=client_secret
            )
        else:
            self.app = PublicClientApplication(client_id, authority=authority)

        self.token_cache = None

    def get_token(self, *scopes, **kwargs) -> AccessToken:
        """Get valid access token (required by Azure SDK)"""
        scopes = scopes or self.scopes

        # Try to get token from cache first
        accounts = self.app.get_accounts()
        if accounts:
            result = self.app.acquire_token_silent(scopes, account=accounts[0])
            if result and "access_token" in result:
                return AccessToken(result["access_token"], int(result["expires_in"] + time.time()))

        # Exchange auth code for token
        if self.auth_code:
            result = self.app.acquire_token_by_authorization_code(
                self.auth_code,
                scopes=scopes,
                redirect_uri=self.redirect_uri
            )

            if "access_token" in result:
                self.auth_code = None  # Clear after use
                return AccessToken(result["access_token"], int(result["expires_in"] + time.time()))
            else:
                raise Exception(f"Token acquisition failed: {result.get('error_description', result)}")

        raise Exception("No access token available. Provide auth_code or valid cached token.")
