from __future__ import annotations

from datetime import timedelta

import httpx

from reyvo.core.config import get_settings
from reyvo.core.exceptions import ServiceUnavailableError
from reyvo.core.utils import utcnow
from reyvo.integrations.email.base import (
    EmailProviderError,
    OAuthTokens,
    OutboundEmail,
    ProviderAccountInfo,
    SendResult,
)

GRAPH_SCOPES = ["offline_access", "User.Read", "Mail.Send", "Mail.ReadWrite"]


class GraphAdapter:
    provider = "microsoft"

    def __init__(self) -> None:
        settings = get_settings()
        self._client_id = settings.microsoft_client_id
        self._client_secret = settings.microsoft_client_secret
        self._tenant = settings.microsoft_tenant_id or "common"
        self._redirect_uri = settings.microsoft_redirect_uri

    @property
    def _authority(self) -> str:
        return f"https://login.microsoftonline.com/{self._tenant}"

    def _require_credentials(self) -> None:
        if not self._client_id or not self._client_secret:
            raise ServiceUnavailableError(
                "Microsoft OAuth is not configured. "
                "Set MICROSOFT_CLIENT_ID and MICROSOFT_CLIENT_SECRET."
            )

    def build_authorization_url(self, *, state: str) -> str:
        self._require_credentials()
        params = {
            "client_id": self._client_id,
            "response_type": "code",
            "redirect_uri": self._redirect_uri,
            "response_mode": "query",
            "scope": " ".join(GRAPH_SCOPES),
            "state": state,
        }
        return str(httpx.URL(f"{self._authority}/oauth2/v2.0/authorize", params=params))

    async def _token_request(self, data: dict[str, str]) -> OAuthTokens:
        self._require_credentials()
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(f"{self._authority}/oauth2/v2.0/token", data=data)
        if response.status_code >= 400:
            raise EmailProviderError(f"Microsoft token exchange failed: {response.text}")
        payload = response.json()
        expires_in = int(payload.get("expires_in", 3600))
        scopes = str(payload.get("scope", "")).split()
        return OAuthTokens(
            access_token=payload["access_token"],
            refresh_token=payload.get("refresh_token"),
            expires_at=utcnow() + timedelta(seconds=expires_in),
            scopes=scopes or list(GRAPH_SCOPES),
        )

    async def exchange_code(self, *, code: str) -> OAuthTokens:
        return await self._token_request(
            {
                "client_id": self._client_id or "",
                "client_secret": self._client_secret or "",
                "code": code,
                "redirect_uri": self._redirect_uri,
                "grant_type": "authorization_code",
            }
        )

    async def refresh_access_token(self, *, refresh_token: str) -> OAuthTokens:
        tokens = await self._token_request(
            {
                "client_id": self._client_id or "",
                "client_secret": self._client_secret or "",
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
                "scope": " ".join(GRAPH_SCOPES),
            }
        )
        if tokens.refresh_token is None:
            tokens.refresh_token = refresh_token
        return tokens

    async def fetch_account_info(self, *, access_token: str) -> ProviderAccountInfo:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                "https://graph.microsoft.com/v1.0/me",
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if response.status_code >= 400:
            raise EmailProviderError(f"Microsoft Graph profile request failed: {response.text}")
        payload = response.json()
        email = payload.get("mail") or payload.get("userPrincipalName")
        if not email:
            raise EmailProviderError("Microsoft Graph did not return an email address.")
        return ProviderAccountInfo(email=email, display_name=payload.get("displayName"))

    async def send_email(self, *, access_token: str, email: OutboundEmail) -> SendResult:
        message: dict[str, object] = {
            "subject": email.subject,
            "body": {"contentType": "HTML", "content": email.body_html},
            "toRecipients": [{"emailAddress": {"address": email.to_email}}],
        }
        if email.reply_to:
            message["replyTo"] = [{"emailAddress": {"address": email.reply_to}}]
        if email.in_reply_to:
            message["internetMessageHeaders"] = [
                {"name": "In-Reply-To", "value": email.in_reply_to},
                {
                    "name": "References",
                    "value": " ".join(email.references or [email.in_reply_to]),
                },
            ]

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                "https://graph.microsoft.com/v1.0/me/sendMail",
                headers={"Authorization": f"Bearer {access_token}"},
                json={"message": message, "saveToSentItems": True},
            )
        if response.status_code >= 400:
            raise EmailProviderError(f"Microsoft Graph send failed: {response.text}")
        return SendResult()
