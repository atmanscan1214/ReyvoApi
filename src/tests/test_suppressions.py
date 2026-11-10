from __future__ import annotations

import uuid

from reyvo.core.security import create_unsubscribe_token


async def test_suppression_lifecycle(client, register_account, auth_headers):
    tokens = await register_account()
    headers = auth_headers(tokens)

    contact = await client.post(
        "/api/v1/contacts", headers=headers, json={"email": "blocked@target.com"}
    )
    assert contact.status_code == 201
    contact_id = contact.json()["id"]

    created = await client.post(
        "/api/v1/suppressions",
        headers=headers,
        json={"email": "Blocked@Target.com", "reason": "manual", "note": "asked us to stop"},
    )
    assert created.status_code == 201, created.text
    assert created.json()["email_normalized"] == "blocked@target.com"

    duplicate = await client.post(
        "/api/v1/suppressions",
        headers=headers,
        json={"email": "blocked@target.com"},
    )
    assert duplicate.status_code == 409

    refreshed = await client.get(f"/api/v1/contacts/{contact_id}", headers=headers)
    assert refreshed.json()["status"] == "unsubscribed"

    listing = await client.get("/api/v1/suppressions", headers=headers)
    assert listing.status_code == 200
    assert listing.json()["total"] == 1

    removed = await client.delete(f"/api/v1/suppressions/{created.json()['id']}", headers=headers)
    assert removed.status_code == 204


async def test_suppressed_contact_is_created_as_unsubscribed(
    client, register_account, auth_headers
):
    tokens = await register_account()
    headers = auth_headers(tokens)

    await client.post("/api/v1/suppressions", headers=headers, json={"email": "gone@target.com"})
    created = await client.post(
        "/api/v1/contacts", headers=headers, json={"email": "Gone@Target.com"}
    )
    assert created.status_code == 201
    assert created.json()["status"] == "unsubscribed"


async def test_public_unsubscribe_flow(client, register_account, auth_headers):
    tokens = await register_account()
    headers = auth_headers(tokens)
    tenant_id = tokens["tenant"]["id"]

    contact = await client.post(
        "/api/v1/contacts", headers=headers, json={"email": "reader@target.com"}
    )
    contact_id = contact.json()["id"]

    token = create_unsubscribe_token(
        tenant_id=uuid.UUID(tenant_id), contact_id=uuid.UUID(contact_id)
    )

    info = await client.get(f"/api/v1/u/{token}")
    assert info.status_code == 200, info.text
    assert info.json()["valid"] is True
    assert info.json()["masked_email"] == "r***@target.com"

    first = await client.post(f"/api/v1/u/{token}", json={"reason": "too many emails"})
    assert first.status_code == 200
    assert first.json()["already_suppressed"] is False

    second = await client.post(f"/api/v1/u/{token}")
    assert second.status_code == 200
    assert second.json()["already_suppressed"] is True

    listing = await client.get("/api/v1/suppressions", headers=headers)
    assert listing.json()["total"] == 1

    refreshed = await client.get(f"/api/v1/contacts/{contact_id}", headers=headers)
    assert refreshed.json()["status"] == "unsubscribed"
