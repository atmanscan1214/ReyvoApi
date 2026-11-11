from __future__ import annotations

import io


async def test_contact_crud_and_search(client, register_account, auth_headers):
    tokens = await register_account()
    headers = auth_headers(tokens)

    company = await client.post(
        "/api/v1/companies",
        headers=headers,
        json={"name": "Blue Ridge Health", "domain": "https://blueridge.test/about"},
    )
    assert company.status_code == 201
    company_id = company.json()["id"]

    created = await client.post(
        "/api/v1/contacts",
        headers=headers,
        json={
            "email": "Dana.Reyes@BlueRidge.com",
            "first_name": "Dana",
            "last_name": "Reyes",
            "title": "Director of Nursing",
            "company_id": company_id,
        },
    )
    assert created.status_code == 201, created.text
    contact = created.json()
    assert contact["email_normalized"] == "dana.reyes@blueridge.com"
    assert contact["full_name"] == "Dana Reyes"
    assert contact["status"] == "new"

    duplicate = await client.post(
        "/api/v1/contacts",
        headers=headers,
        json={"email": "dana.reyes@blueridge.com"},
    )
    assert duplicate.status_code == 409

    updated = await client.patch(
        f"/api/v1/contacts/{contact['id']}",
        headers=headers,
        json={"title": "VP of Nursing", "status": "contacted"},
    )
    assert updated.status_code == 200
    assert updated.json()["title"] == "VP of Nursing"
    assert updated.json()["status"] == "contacted"

    listing = await client.get("/api/v1/contacts", headers=headers, params={"search": "dana"})
    assert listing.status_code == 200
    assert listing.json()["total"] == 1

    deleted = await client.delete(f"/api/v1/contacts/{contact['id']}", headers=headers)
    assert deleted.status_code == 204

    missing = await client.get(f"/api/v1/contacts/{contact['id']}", headers=headers)
    assert missing.status_code == 404


async def test_contact_import_and_export(client, register_account, auth_headers):
    tokens = await register_account()
    headers = auth_headers(tokens)

    csv_body = (
        "email,first_name,last_name,title,company\n"
        "one@target.com,One,Person,Recruiter,Target Co\n"
        "two@target.com,Two,Person,Manager,Target Co\n"
        "not-an-email,Bad,Row,,\n"
        "one@target.com,Duplicate,Row,,\n"
    )
    response = await client.post(
        "/api/v1/contacts/import",
        headers=headers,
        files={"file": ("contacts.csv", io.BytesIO(csv_body.encode()), "text/csv")},
    )
    assert response.status_code == 201, response.text
    report = response.json()
    assert report["imported_count"] == 2
    assert report["duplicate_count"] == 1
    assert report["error_count"] == 1

    export = await client.get("/api/v1/contacts/export", headers=headers)
    assert export.status_code == 200
    assert "one@target.com" in export.text
    assert "two@target.com" in export.text


async def test_contact_lists(client, register_account, auth_headers):
    tokens = await register_account()
    headers = auth_headers(tokens)

    contact = await client.post(
        "/api/v1/contacts", headers=headers, json={"email": "list@target.com"}
    )
    contact_id = contact.json()["id"]

    created = await client.post(
        "/api/v1/lists",
        headers=headers,
        json={"name": "Q1 Outreach", "contact_ids": [contact_id]},
    )
    assert created.status_code == 201
    list_id = created.json()["id"]

    lists = await client.get("/api/v1/lists", headers=headers)
    assert lists.status_code == 200
    assert lists.json()[0]["contact_count"] == 1

    members = await client.get(f"/api/v1/lists/{list_id}/contacts", headers=headers)
    assert members.status_code == 200
    assert members.json()["total"] == 1

    removed = await client.delete(f"/api/v1/lists/{list_id}/contacts/{contact_id}", headers=headers)
    assert removed.status_code == 204

    deleted = await client.delete(f"/api/v1/lists/{list_id}", headers=headers)
    assert deleted.status_code == 204
