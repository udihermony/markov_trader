from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_db
from backend.api.main import app

VALID_SPEC = {
    "name": "sma-crossover",
    "sources": [{"id": "px", "type": "price_bars"}],
    "nodes": [
        {"id": "u1", "kind": "universe", "type": "manual_list", "params": {"tickers": ["AAPL"]}},
        {"id": "t1", "kind": "trigger", "type": "cross",
         "params": {"a": "sma(px.close, 10)", "b": "sma(px.close, 20)", "direction": "up"}},
        {"id": "x1", "kind": "exit", "type": "cross",
         "params": {"a": "sma(px.close, 10)", "b": "sma(px.close, 20)", "direction": "down"}},
        {"id": "x2", "kind": "exit", "type": "time_stop",
         "params": {"max_hold_days": 5, "calendar_feature": "px.close"}},
        {"id": "s1", "kind": "size", "type": "fixed_fraction", "params": {"fraction": 0.1}},
    ],
    "edges": [["u1", "t1"]],
}

CYCLIC_SPEC = {
    "name": "cyclic",
    "sources": [{"id": "px", "type": "price_bars"}],
    "nodes": [
        {"id": "u1", "kind": "universe", "type": "manual_list", "params": {"tickers": ["AAPL"]}},
        {"id": "u2", "kind": "universe", "type": "manual_list", "params": {"tickers": ["MSFT"]}},
        {"id": "t1", "kind": "trigger", "type": "cross",
         "params": {"a": "sma(px.close, 10)", "b": "sma(px.close, 20)", "direction": "up"}},
        {"id": "x1", "kind": "exit", "type": "time_stop",
         "params": {"max_hold_days": 5, "calendar_feature": "px.close"}},
        {"id": "s1", "kind": "size", "type": "fixed_fraction", "params": {"fraction": 0.1}},
    ],
    "edges": [["u1", "u2"], ["u2", "u1"]],
}


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


def _register_and_login(client, email="strat-user@example.com") -> str:
    client.post("/auth/register", json={"email": email, "password": "hunter2pw"})
    res = client.post("/auth/login", json={"email": email, "password": "hunter2pw"})
    return res.json()["access_token"]


def _auth_headers(client, email="strat-user@example.com") -> dict:
    token = _register_and_login(client, email)
    return {"Authorization": f"Bearer {token}"}


def test_create_valid_strategy(client):
    headers = _auth_headers(client)
    res = client.post("/strategies", json={"name": "My SMA", "spec": VALID_SPEC}, headers=headers)
    assert res.status_code == 201
    body = res.json()
    assert body["name"] == "My SMA"
    assert body["spec_version"] == 2
    assert body["spec"]["nodes"][0]["type"] == "manual_list"


def test_create_cyclic_spec_rejected(client):
    headers = _auth_headers(client)
    res = client.post("/strategies", json={"name": "Bad", "spec": CYCLIC_SPEC}, headers=headers)
    assert res.status_code == 422


def test_list_scoped_to_owner(client):
    headers_a = _auth_headers(client, "a@example.com")
    headers_b = _auth_headers(client, "b@example.com")
    client.post("/strategies", json={"name": "A's strategy", "spec": VALID_SPEC}, headers=headers_a)

    # each user already has a "SPY Buy & Hold" strategy from registration's
    # default benchmark wallet, plus A's explicitly created one
    res_a = client.get("/strategies", headers=headers_a)
    res_b = client.get("/strategies", headers=headers_b)
    assert len(res_a.json()) == 2
    assert len(res_b.json()) == 1


def test_get_unowned_strategy_returns_404(client):
    headers_a = _auth_headers(client, "a2@example.com")
    headers_b = _auth_headers(client, "b2@example.com")
    created = client.post(
        "/strategies", json={"name": "A's strategy", "spec": VALID_SPEC}, headers=headers_a
    ).json()

    res = client.get(f"/strategies/{created['id']}", headers=headers_b)
    assert res.status_code == 404


def test_get_missing_strategy_returns_404(client):
    headers = _auth_headers(client, "c@example.com")
    res = client.get("/strategies/999999", headers=headers)
    assert res.status_code == 404
