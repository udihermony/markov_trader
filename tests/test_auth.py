from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_db
from backend.api.main import app


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


def test_health(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_register_then_login(client):
    res = client.post("/auth/register", json={"email": "a@example.com", "password": "hunter2pw"})
    assert res.status_code == 201
    body = res.json()
    assert body["email"] == "a@example.com"
    assert "id" in body

    res = client.post("/auth/login", json={"email": "a@example.com", "password": "hunter2pw"})
    assert res.status_code == 200
    body = res.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]


def test_register_duplicate_email_rejected(client):
    client.post("/auth/register", json={"email": "dup@example.com", "password": "hunter2pw"})
    res = client.post("/auth/register", json={"email": "dup@example.com", "password": "other-pw"})
    assert res.status_code == 409


def test_login_wrong_password_rejected(client):
    client.post("/auth/register", json={"email": "b@example.com", "password": "correct-pw"})
    res = client.post("/auth/login", json={"email": "b@example.com", "password": "wrong-pw"})
    assert res.status_code == 401


def test_login_unknown_email_rejected(client):
    res = client.post("/auth/login", json={"email": "nobody@example.com", "password": "x"})
    assert res.status_code == 401
