from __future__ import annotations

import base64
from datetime import timedelta
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

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

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
GMAIL_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"

GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
]


class GmailAdapter:
    provider = "google"

    def __init__(self) -> None:
        settings = get_settings()
        self._client_id = settings.google_client_id
        self._client_secret = settings.google_client_secret
        self._redirect_uri = settings.google_redirect_uri

    def _require_credentials(self) -> None:
        if not self._client_id or not self._client_secret:
            raise ServiceUnavailableError(
                "Google OAuth is not configured. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET."
            )

    def build_authorization_url(self, *, state: str) -> str:
        self._require_credentials()
        params = {
            "client_id": self._client_id,
            "redirect_uri": self._redirect_uri,
            "response_type": "code",
            "scope": " ".join(GMAIL_SCOPES),
            "access_type": "offline",
            "include_granted_scopes": "true",
            "prompt": "consent",
            "state": state,
        }
        return str(httpx.URL(AUTH_URL, params=params))

    async def _token_request(self, data: dict[str, str]) -> OAuthTokens:
        self._require_credentials()
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(TOKEN_URL, data=data)
        if response.status_code >= 400:
            raise EmailProviderError(f"Google token exchange failed: {response.text}")
        payload = response.json()
        expires_in = int(payload.get("expires_in", 3600))
        scopes = str(payload.get("scope", "")).split()
        return OAuthTokens(
            access_token=payload["access_token"],
            refresh_token=payload.get("refresh_token"),
            expires_at=utcnow() + timedelta(seconds=expires_in),
            scopes=scopes or list(GMAIL_SCOPES),
        )

    async def exchange_code(self, *, code: str) -> OAuthTokens:
        return await self._token_request(
            {
                "code": code,
                "client_id": self._client_id or "",
                "client_secret": self._client_secret or "",
                "redirect_uri": self._redirect_uri,
                "grant_type": "authorization_code",
            }
        )

    async def refresh_access_token(self, *, refresh_token: str) -> OAuthTokens:
        tokens = await self._token_request(
            {
                "refresh_token": refresh_token,
                "client_id": self._client_id or "",
                "client_secret": self._client_secret or "",
                "grant_type": "refresh_token",
            }
        )
        if tokens.refresh_token is None:
            tokens.refresh_token = refresh_token
        return tokens

    async def fetch_account_info(self, *, access_token: str) -> ProviderAccountInfo:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}
            )
        if response.status_code >= 400:
            raise EmailProviderError(f"Google userinfo request failed: {response.text}")
        payload = response.json()
        return ProviderAccountInfo(
            email=payload["email"],
            display_name=payload.get("name"),
        )

    async def send_email(self, *, access_token: str, email: OutboundEmail) -> SendResult:
        message = EmailMessage()
        from_email = (await self.fetch_account_info(access_token=access_token)).email
        message["From"] = (
            formataddr((email.from_name, from_email)) if email.from_name else from_email
        )
        message["To"] = email.to_email
        message["Subject"] = email.subject
        rfc_message_id = make_msgid(domain=from_email.split("@", 1)[-1])
        message["Message-ID"] = rfc_message_id
        if email.reply_to:
            message["Reply-To"] = email.reply_to
        if email.in_reply_to:
            message["In-Reply-To"] = email.in_reply_to
        if email.references:
            message["References"] = " ".join(email.references)

        message.set_content(email.body_text or _strip_html(email.body_html))
        message.add_alternative(email.body_html, subtype="html")

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                GMAIL_SEND_URL,
                headers={"Authorization": f"Bearer {access_token}"},
                json={"raw": raw},
            )
        if response.status_code >= 400:
            raise EmailProviderError(f"Gmail send failed: {response.text}")
        payload = response.json()
        return SendResult(
            provider_message_id=payload.get("id"),
            provider_thread_id=payload.get("threadId"),
            rfc_message_id=rfc_message_id,
        )


def _strip_html(value: str) -> str:
    import re

    return re.sub(r"<[^>]+>", "", value)
