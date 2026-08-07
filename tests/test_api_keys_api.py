from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.api.deps import get_db
from backend.api.main import app
from backend.db.models import ApiKey


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


def _auth_headers(client, email="apikey-user@example.com") -> dict:
    client.post("/auth/register", json={"email": email, "password": "hunter2pw"})
    res = client.post("/auth/login", json={"email": email, "password": "hunter2pw"})
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


def test_save_then_list_round_trips_without_exposing_the_key(client, db_session):
    headers = _auth_headers(client)
    res = client.post("/api-keys", json={"provider": "anthropic", "key": "sk-ant-super-secret"}, headers=headers)
    assert res.status_code == 201
    assert "key" not in res.json()
    assert res.json()["provider"] == "anthropic"

    listed = client.get("/api-keys", headers=headers).json()
    assert len(listed) == 1
    assert listed[0]["provider"] == "anthropic"
    assert "key" not in listed[0]


def test_stored_value_is_encrypted_not_plaintext(client, db_session):
    headers = _auth_headers(client)
    client.post("/api-keys", json={"provider": "anthropic", "key": "sk-ant-super-secret"}, headers=headers)

    row = db_session.execute(select(ApiKey)).scalar_one()
    assert row.encrypted_key != "sk-ant-super-secret"
    assert "sk-ant-super-secret" not in row.encrypted_key


def test_saving_again_upserts_rather_than_duplicates(client, db_session):
    headers = _auth_headers(client)
    client.post("/api-keys", json={"provider": "anthropic", "key": "sk-ant-first"}, headers=headers)
    client.post("/api-keys", json={"provider": "anthropic", "key": "sk-ant-second"}, headers=headers)

    listed = client.get("/api-keys", headers=headers).json()
    assert len(listed) == 1


def test_delete_removes_the_key(client, db_session):
    headers = _auth_headers(client)
    client.post("/api-keys", json={"provider": "anthropic", "key": "sk-ant-super-secret"}, headers=headers)

    res = client.delete("/api-keys/anthropic", headers=headers)
    assert res.status_code == 204
    assert client.get("/api-keys", headers=headers).json() == []


def test_delete_missing_key_returns_404(client, db_session):
    headers = _auth_headers(client)
    res = client.delete("/api-keys/anthropic", headers=headers)
    assert res.status_code == 404


def test_unsupported_provider_rejected(client, db_session):
    headers = _auth_headers(client)
    res = client.post("/api-keys", json={"provider": "openai", "key": "sk-whatever"}, headers=headers)
    assert res.status_code == 422


def test_keys_scoped_to_owner(client, db_session):
    headers_a = _auth_headers(client, "a@example.com")
    headers_b = _auth_headers(client, "b@example.com")
    client.post("/api-keys", json={"provider": "anthropic", "key": "sk-ant-a"}, headers=headers_a)

    assert len(client.get("/api-keys", headers=headers_a).json()) == 1
    assert len(client.get("/api-keys", headers=headers_b).json()) == 0
