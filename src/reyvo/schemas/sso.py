from __future__ import annotations

from pydantic import BaseModel, Field


class SSOStatus(BaseModel):
    provider: str = "workos"
    available: bool
    configured: bool
    enabled: bool
    organization_id: str | None = None
    connection_id: str | None = None
    redirect_uri: str | None = None


class SSOSettingsUpdate(BaseModel):
    workos_organization_id: str | None = Field(default=None, max_length=255)
    workos_sso_connection_id: str | None = Field(default=None, max_length=255)


class SSOAuthorizeResponse(BaseModel):
    authorization_url: str
    state: str


class SSOCallbackRequest(BaseModel):
    code: str = Field(min_length=1)
    state: str = Field(min_length=1)
