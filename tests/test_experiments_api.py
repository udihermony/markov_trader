from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_db
from backend.api.main import app
from backend.db.models import Instrument, PriceBar
from backend.sources.price_bars import PriceBarsSource

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

START = date(2026, 2, 2)
END = date(2026, 3, 6)


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
    d = START - timedelta(days=120)
    while d <= END:
        if d.weekday() < 5:
            db_session.add(
                PriceBar(instrument_id=instrument.id, date=d, open=100, high=101, low=99, close=100, volume=1000)
            )
        d += timedelta(days=1)
    db_session.flush()


def _auth_headers(client, email="lab-user@example.com") -> dict:
    client.post("/auth/register", json={"email": email, "password": "hunter2pw"})
    res = client.post("/auth/login", json={"email": email, "password": "hunter2pw"})
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


def _create_strategy(client, headers, spec=ALWAYS_BUY_SPEC, name="Always Buy"):
    res = client.post("/strategies", json={"name": name, "spec": spec}, headers=headers)
    assert res.status_code == 201
    return res.json()


def test_hypothesis_and_expected_outcome_required(client, db_session):
    headers = _auth_headers(client)
    strategy = _create_strategy(client, headers)
    res = client.post(
        "/experiments",
        json={"strategy_id": strategy["id"], "period_start": str(START), "period_end": str(END)},
        headers=headers,
    )
    assert res.status_code == 422


def test_create_experiment_runs_backtest_and_persists(client, db_session):
    headers = _auth_headers(client)
    strategy = _create_strategy(client, headers)
    _seed_bars(db_session, "TICK")
    _seed_bars(db_session, "SPY")

    res = client.post(
        "/experiments",
        json={
            "strategy_id": strategy["id"], "hypothesis": "It buys and holds",
            "expected_outcome": "Roughly flat, minus slippage",
            "period_start": str(START), "period_end": str(END),
        },
        headers=headers,
    )
    assert res.status_code == 201
    body = res.json()
    assert body["actual_outcome"] is not None
    assert "trades" in body["actual_outcome"]
    assert body["prediction_correct"] is None
    assert body["is_holdout"] is False
    assert body["result_json"]["metrics"]["n_trades"] == 1
    assert body["result_json"]["metrics"]["total_return_pct"] <= 0  # flat prices + slippage


def test_list_includes_diff_summary(client, db_session):
    headers = _auth_headers(client)
    strategy = _create_strategy(client, headers)
    _seed_bars(db_session, "TICK")
    _seed_bars(db_session, "SPY")

    body = {
        "strategy_id": strategy["id"], "hypothesis": "h1", "expected_outcome": "e1",
        "period_start": str(START), "period_end": str(END),
    }
    client.post("/experiments", json=body, headers=headers)

    changed_spec = {**ALWAYS_BUY_SPEC, "nodes": [
        ALWAYS_BUY_SPEC["nodes"][0], ALWAYS_BUY_SPEC["nodes"][1], ALWAYS_BUY_SPEC["nodes"][2],
        {"id": "s1", "kind": "size", "type": "fixed_fraction", "params": {"fraction": 0.8}},
    ]}
    client.put(f"/strategies/{strategy['id']}", json={"spec": changed_spec}, headers=headers)
    client.post("/experiments", json={**body, "hypothesis": "h2", "expected_outcome": "e2"}, headers=headers)

    listed = client.get(f"/experiments?strategy_id={strategy['id']}", headers=headers).json()
    assert len(listed) == 2
    assert listed[0]["diff_summary"] == "baseline"
    assert "fraction: 0.5" in listed[1]["diff_summary"]
    assert "0.8" in listed[1]["diff_summary"]


def test_prediction_correct_endpoint(client, db_session):
    headers = _auth_headers(client)
    strategy = _create_strategy(client, headers)
    _seed_bars(db_session, "TICK")
    _seed_bars(db_session, "SPY")

    created = client.post(
        "/experiments",
        json={
            "strategy_id": strategy["id"], "hypothesis": "h", "expected_outcome": "e",
            "period_start": str(START), "period_end": str(END),
        },
        headers=headers,
    ).json()

    res = client.post(f"/experiments/{created['id']}/prediction-correct", json={"correct": True}, headers=headers)
    assert res.status_code == 200
    assert res.json()["prediction_correct"] is True


def test_neighbourhood_scan_persists_a_point_per_value_and_moves_the_search_counter(client, db_session):
    headers = _auth_headers(client)
    strategy = _create_strategy(client, headers)
    _seed_bars(db_session, "TICK")
    _seed_bars(db_session, "SPY")

    res = client.post(
        "/experiments/neighbourhood-scan",
        json={
            "strategy_id": strategy["id"], "node_id": "s1", "param_name": "fraction",
            "values": [0.3, 0.5, 0.7], "period_start": str(START), "period_end": str(END),
            "hypothesis": "size doesn't matter much near 0.5", "expected_outcome": "similar returns across the range",
        },
        headers=headers,
    )
    assert res.status_code == 200
    points = res.json()
    assert len(points) == 3
    assert {p["value"] for p in points} == {0.3, 0.5, 0.7}

    counter = client.get(f"/strategies/{strategy['id']}/search-counter", headers=headers).json()
    assert counter["count"] == 3
    assert counter["best_return_pct"] is not None


def test_luck_test_persists_nothing(client, db_session):
    headers = _auth_headers(client)
    strategy = _create_strategy(client, headers)
    _seed_bars(db_session, "TICK")
    _seed_bars(db_session, "SPY")

    res = client.post(
        "/experiments/luck-test",
        json={"strategy_id": strategy["id"], "period_start": str(START), "period_end": str(END), "n_shuffles": 5},
        headers=headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert len(body["shuffled_returns"]) == 5
    assert 0 <= body["percentile"] <= 100

    counter = client.get(f"/strategies/{strategy['id']}/search-counter", headers=headers).json()
    assert counter["count"] == 0
    listed = client.get(f"/experiments?strategy_id={strategy['id']}", headers=headers).json()
    assert listed == []
