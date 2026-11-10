from __future__ import annotations


async def test_contacts_are_isolated_between_tenants(client, register_account, auth_headers):
    tenant_a = await register_account(email="a@example.com", agency_name="Agency A")
    tenant_b = await register_account(email="b@example.com", agency_name="Agency B")

    created = await client.post(
        "/api/v1/contacts",
        headers=auth_headers(tenant_a),
        json={"email": "lead@target.com", "first_name": "Lee", "last_name": "Target"},
    )
    assert created.status_code == 201, created.text
    contact_id = created.json()["id"]

    read = await client.get(f"/api/v1/contacts/{contact_id}", headers=auth_headers(tenant_b))
    assert read.status_code == 404

    update = await client.patch(
        f"/api/v1/contacts/{contact_id}",
        headers=auth_headers(tenant_b),
        json={"first_name": "Hijacked"},
    )
    assert update.status_code == 404

    delete = await client.delete(f"/api/v1/contacts/{contact_id}", headers=auth_headers(tenant_b))
    assert delete.status_code == 404

    listing = await client.get("/api/v1/contacts", headers=auth_headers(tenant_b))
    assert listing.status_code == 200
    assert listing.json()["total"] == 0

    own_listing = await client.get("/api/v1/contacts", headers=auth_headers(tenant_a))
    assert own_listing.json()["total"] == 1


async def test_companies_are_isolated_between_tenants(client, register_account, auth_headers):
    tenant_a = await register_account(email="a@example.com", agency_name="Agency A")
    tenant_b = await register_account(email="b@example.com", agency_name="Agency B")

    created = await client.post(
        "/api/v1/companies",
        headers=auth_headers(tenant_a),
        json={"name": "Target Corp", "domain": "target.test"},
    )
    assert created.status_code == 201
    company_id = created.json()["id"]

    read = await client.get(f"/api/v1/companies/{company_id}", headers=auth_headers(tenant_b))
    assert read.status_code == 404


async def test_audit_log_is_tenant_scoped(client, register_account, auth_headers):
    tenant_a = await register_account(email="a@example.com", agency_name="Agency A")
    await register_account(email="b@example.com", agency_name="Agency B")

    await client.post(
        "/api/v1/companies",
        headers=auth_headers(tenant_a),
        json={"name": "Visible Corp"},
    )

    response = await client.get(
        "/api/v1/tenants/current/audit-logs", headers=auth_headers(tenant_a)
    )
    assert response.status_code == 200
    actions = [item["action"] for item in response.json()["items"]]
    assert "company.created" in actions
