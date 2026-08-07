from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_db
from backend.api.main import app
from backend.api.routers import holdouts as holdouts_router
from backend.db.models import Instrument, PriceBar
from backend.sources.price_bars import PriceBarsSource
from backend.sources.registry import TrustClass

ALWAYS_BUY_SPEC = {
    "spec_version": 2,
    "name": "Always Buy",
    "sources": [{"id": "px", "type": "price_bars"}],
    "nodes": [
        {"id": "u1", "kind": "universe", "type": "manual_list", "params": {"tickers": ["TICK"]}},
        {"id": "t1", "kind": "trigger", "type": "always", "params": {}},
        {"id": "x1", "kind": "exit", "type": "never", "params": {}},
        {"id": "s1", "kind": "size", "type": "fixed_fraction", "params": {"fraction": 0.5}},
    ],
    "edges": [["u1", "t1"]],
}

HOLDOUT_START = date(2026, 1, 5)
HOLDOUT_END = date(2026, 1, 30)


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(PriceBarsSource, "_refresh_one", lambda self, ticker, start, end: None)


def _seed_bars(db_session, ticker: str) -> None:
    instrument = Instrument(ticker=ticker)
    db_session.add(instrument)
    db_session.flush()
    d = HOLDOUT_START - timedelta(days=120)
    while d <= HOLDOUT_END:
        if d.weekday() < 5:
            db_session.add(
                PriceBar(instrument_id=instrument.id, date=d, open=100, high=101, low=99, close=100, volume=1000)
            )
        d += timedelta(days=1)
    db_session.flush()


def _auth_headers(client, email="holdout-user@example.com") -> dict:
    client.post("/auth/register", json={"email": email, "password": "hunter2pw"})
    res = client.post("/auth/login", json={"email": email, "password": "hunter2pw"})
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


def _create_strategy(client, headers):
    res = client.post("/strategies", json={"name": "Always Buy", "spec": ALWAYS_BUY_SPEC}, headers=headers)
    assert res.status_code == 201
    return res.json()


def test_get_holdout_returns_none_when_unsealed(client):
    headers = _auth_headers(client)
    res = client.get("/holdouts", headers=headers)
    assert res.status_code == 200
    assert res.json() is None


def test_seal_once_then_409_on_second_attempt(client):
    headers = _auth_headers(client)
    res1 = client.post(
        "/holdouts", json={"start_date": str(HOLDOUT_START), "end_date": str(HOLDOUT_END)}, headers=headers
    )
    assert res1.status_code == 201
    assert res1.json()["unseals_total"] == 3
    assert res1.json()["unseals_used"] == 0

    res2 = client.post(
        "/holdouts", json={"start_date": str(HOLDOUT_START), "end_date": str(HOLDOUT_END)}, headers=headers
    )
    assert res2.status_code == 409


def test_seal_rejects_a_period_that_has_not_elapsed(client):
    headers = _auth_headers(client)
    res = client.post(
        "/holdouts",
        json={"start_date": "2026-01-01", "end_date": "2099-01-01"},
        headers=headers,
    )
    assert res.status_code == 422


def test_unseal_decrements_budget_and_creates_is_holdout_experiment(client, db_session):
    headers = _auth_headers(client)
    strategy = _create_strategy(client, headers)
    _seed_bars(db_session, "TICK")
    _seed_bars(db_session, "SPY")
    holdout = client.post(
        "/holdouts", json={"start_date": str(HOLDOUT_START), "end_date": str(HOLDOUT_END), "unseals_total": 2},
        headers=headers,
    ).json()

    res = client.post(
        f"/holdouts/{holdout['id']}/unseal",
        json={"strategy_id": strategy["id"], "hypothesis": "h", "expected_outcome": "e"},
        headers=headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["holdout"]["unseals_used"] == 1
    assert body["experiment"]["is_holdout"] is True
    assert body["experiment"]["period_start"] == str(HOLDOUT_START)
    assert body["experiment"]["period_end"] == str(HOLDOUT_END)


def test_unseal_blocked_once_budget_exhausted(client, db_session):
    headers = _auth_headers(client)
    strategy = _create_strategy(client, headers)
    _seed_bars(db_session, "TICK")
    _seed_bars(db_session, "SPY")
    holdout = client.post(
        "/holdouts", json={"start_date": str(HOLDOUT_START), "end_date": str(HOLDOUT_END), "unseals_total": 1},
        headers=headers,
    ).json()

    payload = {"strategy_id": strategy["id"], "hypothesis": "h", "expected_outcome": "e"}
    res1 = client.post(f"/holdouts/{holdout['id']}/unseal", json=payload, headers=headers)
    assert res1.status_code == 200

    res2 = client.post(f"/holdouts/{holdout['id']}/unseal", json=payload, headers=headers)
    assert res2.status_code == 403


def test_unseal_blocked_for_real_ai_veto_strategy(client, db_session):
    # No monkeypatch — this is M10's own live_only source making the M7
    # guard reachable for real, not just via a faked graph.
    ai_spec = {
        **ALWAYS_BUY_SPEC,
        "sources": [*ALWAYS_BUY_SPEC["sources"], {"id": "ai", "type": "ai_judgment"}],
        "nodes": [
            *ALWAYS_BUY_SPEC["nodes"],
            {"id": "v1", "kind": "veto", "type": "ai_regime_check", "params": {}},
        ],
        "edges": [*ALWAYS_BUY_SPEC["edges"], ["t1", "v1"]],
    }
    headers = _auth_headers(client, "ai-holdout@example.com")
    strategy = client.post("/strategies", json={"name": "AI Gated", "spec": ai_spec}, headers=headers).json()
    assert strategy["trust_label"] == "live_only"
    holdout = client.post(
        "/holdouts", json={"start_date": str(HOLDOUT_START), "end_date": str(HOLDOUT_END)}, headers=headers
    ).json()

    res = client.post(
        f"/holdouts/{holdout['id']}/unseal",
        json={"strategy_id": strategy["id"], "hypothesis": "h", "expected_outcome": "e"},
        headers=headers,
    )
    assert res.status_code == 422


def test_unseal_blocked_for_live_only_strategy(client, db_session, monkeypatch):
    headers = _auth_headers(client)
    strategy = _create_strategy(client, headers)
    holdout = client.post(
        "/holdouts", json={"start_date": str(HOLDOUT_START), "end_date": str(HOLDOUT_END)}, headers=headers
    ).json()

    class _FakeLiveOnlyGraph:
        trust_label = TrustClass.LIVE_ONLY

    monkeypatch.setattr(holdouts_router, "_compile_graph", lambda db, spec: _FakeLiveOnlyGraph())

    res = client.post(
        f"/holdouts/{holdout['id']}/unseal",
        json={"strategy_id": strategy["id"], "hypothesis": "h", "expected_outcome": "e"},
        headers=headers,
    )
    assert res.status_code == 422
