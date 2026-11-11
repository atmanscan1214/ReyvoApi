from __future__ import annotations


async def _create_campaign(client, headers, contact_ids, email_account_ids=None):
    return await client.post(
        "/api/v1/campaigns",
        headers=headers,
        json={
            "name": "Nursing leaders Q1",
            "audience_type": "contacts",
            "contact_ids": contact_ids,
            "email_account_ids": email_account_ids or [],
            "daily_cap": 50,
            "steps": [
                {
                    "step_order": 1,
                    "delay_business_days": 0,
                    "subject_template": "Quick question for {{first_name}}",
                    "body_html_template": (
                        "<p>Hi {{first_name}}, can we talk about {{company}}?</p>"
                    ),
                    "body_text_template": "Hi {{first_name}}, can we talk about {{company}}?",
                }
            ],
        },
    )


async def test_campaign_lifecycle(client, register_account, auth_headers, seed_email_account):
    tokens = await register_account()
    headers = auth_headers(tokens)
    tenant_id = tokens["tenant"]["id"]
    account_id = await seed_email_account(tenant_id)

    contact = await client.post(
        "/api/v1/contacts",
        headers=headers,
        json={"email": "lead@target.com", "first_name": "Lee", "last_name": "Lead"},
    )
    contact_id = contact.json()["id"]

    created = await _create_campaign(client, headers, [contact_id], [str(account_id)])
    assert created.status_code == 201, created.text
    campaign = created.json()
    assert campaign["status"] == "draft"
    assert len(campaign["steps"]) == 1

    preview = await client.post(
        f"/api/v1/campaigns/{campaign['id']}/preview",
        headers=headers,
        json={"contact_id": contact_id, "step_order": 1},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["subject"] == "Quick question for Lee"

    preflight = await client.get(f"/api/v1/campaigns/{campaign['id']}/preflight", headers=headers)
    assert preflight.status_code == 200
    assert preflight.json()["ok"] is True
    assert preflight.json()["sendable"] == 1

    submitted = await client.post(f"/api/v1/campaigns/{campaign['id']}/submit", headers=headers)
    assert submitted.status_code == 200
    assert submitted.json()["status"] == "pending_approval"

    approved = await client.post(f"/api/v1/campaigns/{campaign['id']}/approve", headers=headers)
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"

    launched = await client.post(f"/api/v1/campaigns/{campaign['id']}/launch", headers=headers)
    assert launched.status_code == 200, launched.text
    assert launched.json()["status"] == "active"

    stats = await client.get(f"/api/v1/campaigns/{campaign['id']}/stats", headers=headers)
    assert stats.status_code == 200
    assert stats.json()["enrolled"] == 1
    assert stats.json()["active"] == 1

    paused = await client.post(f"/api/v1/campaigns/{campaign['id']}/pause", headers=headers)
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"

    resumed = await client.post(f"/api/v1/campaigns/{campaign['id']}/resume", headers=headers)
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "active"


async def test_launch_preflight_blocks_suppressed_audience(client, register_account, auth_headers):
    tokens = await register_account()
    headers = auth_headers(tokens)

    contact = await client.post(
        "/api/v1/contacts", headers=headers, json={"email": "blocked@target.com"}
    )
    contact_id = contact.json()["id"]

    campaign = await _create_campaign(client, headers, [contact_id])
    assert campaign.status_code == 201

    await client.post("/api/v1/suppressions", headers=headers, json={"email": "blocked@target.com"})

    preflight = await client.get(
        f"/api/v1/campaigns/{campaign.json()['id']}/preflight", headers=headers
    )
    assert preflight.status_code == 200
    assert preflight.json()["ok"] is False
    assert any("suppressed" in message for message in preflight.json()["errors"])


async def test_campaign_cannot_launch_before_approval(client, register_account, auth_headers):
    tokens = await register_account()
    headers = auth_headers(tokens)
    contact = await client.post(
        "/api/v1/contacts", headers=headers, json={"email": "lead@target.com"}
    )
    campaign = await _create_campaign(client, headers, [contact.json()["id"]])

    response = await client.post(
        f"/api/v1/campaigns/{campaign.json()['id']}/launch", headers=headers
    )
    assert response.status_code == 400


async def test_campaign_step_update_replaces_steps(client, register_account, auth_headers):
    tokens = await register_account()
    headers = auth_headers(tokens)
    contact = await client.post(
        "/api/v1/contacts", headers=headers, json={"email": "lead@target.com"}
    )
    campaign = await _create_campaign(client, headers, [contact.json()["id"]])

    updated = await client.patch(
        f"/api/v1/campaigns/{campaign.json()['id']}",
        headers=headers,
        json={
            "steps": [
                {
                    "step_order": 1,
                    "delay_business_days": 0,
                    "subject_template": "Updated subject for {{first_name}}",
                    "body_html_template": "<p>Updated</p>",
                }
            ]
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["steps"][0]["subject_template"] == ("Updated subject for {{first_name}}")
    assert updated.json()["approval_status"] == "pending"
