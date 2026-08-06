from __future__ import annotations

from datetime import date, timedelta

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


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


def _auth_headers(client, email="wallet-user@example.com") -> dict:
    client.post("/auth/register", json={"email": email, "password": "hunter2pw"})
    token = client.post("/auth/login", json={"email": email, "password": "hunter2pw"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _create_strategy(client, headers) -> int:
    res = client.post("/strategies", json={"name": "My SMA", "spec": VALID_SPEC}, headers=headers)
    return res.json()["id"]


def test_create_wallet_defaults_start_date_to_today(client):
    headers = _auth_headers(client)
    strategy_id = _create_strategy(client, headers)
    res = client.post(
        "/wallets", json={"name": "My Wallet", "strategy_id": strategy_id}, headers=headers
    )
    assert res.status_code == 201
    body = res.json()
    assert body["start_date"] == date.today().isoformat()
    assert body["status"] == "active"
    assert body["is_benchmark"] is False


def test_create_wallet_backdated_rejected(client):
    headers = _auth_headers(client)
    strategy_id = _create_strategy(client, headers)
    past = (date.today() - timedelta(days=1)).isoformat()
    res = client.post(
        "/wallets",
        json={"name": "Backdated", "strategy_id": strategy_id, "start_date": past},
        headers=headers,
    )
    assert res.status_code == 400
    assert "backdated" in res.json()["detail"]


def test_create_wallet_unowned_strategy_returns_404(client):
    headers_a = _auth_headers(client, "wa@example.com")
    headers_b = _auth_headers(client, "wb@example.com")
    strategy_id = _create_strategy(client, headers_a)
    res = client.post(
        "/wallets", json={"name": "Sneaky", "strategy_id": strategy_id}, headers=headers_b
    )
    assert res.status_code == 404


def test_create_wallet_missing_strategy_returns_404(client):
    headers = _auth_headers(client, "wc@example.com")
    res = client.post(
        "/wallets", json={"name": "No strategy", "strategy_id": 999999}, headers=headers
    )
    assert res.status_code == 404


def test_retire_wallet(client):
    headers = _auth_headers(client, "wd@example.com")
    strategy_id = _create_strategy(client, headers)
    wallet = client.post(
        "/wallets", json={"name": "Retire me", "strategy_id": strategy_id}, headers=headers
    ).json()

    res = client.post(f"/wallets/{wallet['id']}/retire", headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "retired"
    assert body["retired_at"] is not None


def test_retire_benchmark_wallet_rejected(client):
    headers = _auth_headers(client, "we@example.com")
    wallets = client.get("/wallets", headers=headers).json()
    benchmark = next(w for w in wallets if w["is_benchmark"])

    res = client.post(f"/wallets/{benchmark['id']}/retire", headers=headers)
    assert res.status_code == 400
    assert "benchmark" in res.json()["detail"]


def test_list_wallets_scoped_to_owner(client):
    headers_a = _auth_headers(client, "wf@example.com")
    headers_b = _auth_headers(client, "wg@example.com")
    strategy_id = _create_strategy(client, headers_a)
    client.post("/wallets", json={"name": "A's wallet", "strategy_id": strategy_id}, headers=headers_a)

    # each user has their own auto-created benchmark wallet + this one for A
    res_a = client.get("/wallets", headers=headers_a).json()
    res_b = client.get("/wallets", headers=headers_b).json()
    assert len(res_a) == 2  # benchmark + created
    assert len(res_b) == 1  # just their own benchmark


def test_get_unowned_wallet_returns_404(client):
    headers_a = _auth_headers(client, "wh@example.com")
    headers_b = _auth_headers(client, "wi@example.com")
    strategy_id = _create_strategy(client, headers_a)
    wallet = client.post(
        "/wallets", json={"name": "A's wallet", "strategy_id": strategy_id}, headers=headers_a
    ).json()

    res = client.get(f"/wallets/{wallet['id']}", headers=headers_b)
    assert res.status_code == 404
