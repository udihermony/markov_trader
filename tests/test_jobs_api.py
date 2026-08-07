from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_db
from backend.api.main import app

VALID_SPEC = {
    "spec_version": 2,
    "name": "sma-crossover",
    "sources": [{"id": "px", "type": "price_bars"}],
    "nodes": [
        {"id": "u1", "kind": "universe", "type": "manual_list", "params": {"tickers": ["AAPL"]}},
        {"id": "t1", "kind": "trigger", "type": "cross",
         "params": {"a": "sma(px.close, 10)", "b": "sma(px.close, 20)", "direction": "up"}},
        {"id": "x1", "kind": "exit", "type": "cross",
         "params": {"a": "sma(px.close, 10)", "b": "sma(px.close, 20)", "direction": "down"}},
        {"id": "s1", "kind": "size", "type": "fixed_fraction", "params": {"fraction": 0.1}},
    ],
    "edges": [["u1", "t1"]],
}


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


def _auth_headers(client, email="jobs-user@example.com") -> dict:
    client.post("/auth/register", json={"email": email, "password": "hunter2pw"})
    res = client.post("/auth/login", json={"email": email, "password": "hunter2pw"})
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


def _create_strategy(client, headers) -> dict:
    res = client.post("/strategies", json={"name": "Root", "spec": VALID_SPEC}, headers=headers)
    assert res.status_code == 201
    return res.json()


def test_create_unattended_session_enqueues_a_pending_job(client):
    headers = _auth_headers(client)
    strategy = _create_strategy(client, headers)

    res = client.post(
        "/jobs/unattended-sessions",
        json={"strategy_id": strategy["id"], "goal": "find something", "budget": 5},
        headers=headers,
    )
    assert res.status_code == 201
    body = res.json()
    assert body["status"] == "pending"
    assert body["type"] == "unattended_experiment_session"
    assert body["payload_json"] == {"strategy_id": strategy["id"], "goal": "find something", "budget": 5}


def test_create_unattended_session_on_unowned_strategy_returns_404(client):
    headers_a = _auth_headers(client, "a@example.com")
    headers_b = _auth_headers(client, "b@example.com")
    strategy = _create_strategy(client, headers_a)

    res = client.post(
        "/jobs/unattended-sessions",
        json={"strategy_id": strategy["id"], "goal": "goal"},
        headers=headers_b,
    )
    assert res.status_code == 404


def test_hypothesis_missing_goal_returns_422(client):
    headers = _auth_headers(client)
    strategy = _create_strategy(client, headers)

    res = client.post(
        "/jobs/unattended-sessions", json={"strategy_id": strategy["id"]}, headers=headers
    )
    assert res.status_code == 422


def test_jobs_scoped_to_owner(client):
    headers_a = _auth_headers(client, "a2@example.com")
    headers_b = _auth_headers(client, "b2@example.com")
    strategy = _create_strategy(client, headers_a)
    client.post(
        "/jobs/unattended-sessions",
        json={"strategy_id": strategy["id"], "goal": "goal"},
        headers=headers_a,
    )

    assert len(client.get("/jobs", headers=headers_a).json()) == 1
    assert len(client.get("/jobs", headers=headers_b).json()) == 0


def test_get_job_scoped_to_owner(client):
    headers_a = _auth_headers(client, "a3@example.com")
    headers_b = _auth_headers(client, "b3@example.com")
    strategy = _create_strategy(client, headers_a)
    created = client.post(
        "/jobs/unattended-sessions",
        json={"strategy_id": strategy["id"], "goal": "goal"},
        headers=headers_a,
    ).json()

    res_a = client.get(f"/jobs/{created['id']}", headers=headers_a)
    assert res_a.status_code == 200

    res_b = client.get(f"/jobs/{created['id']}", headers=headers_b)
    assert res_b.status_code == 404


def test_get_missing_job_returns_404(client):
    headers = _auth_headers(client)
    res = client.get("/jobs/999999", headers=headers)
    assert res.status_code == 404
