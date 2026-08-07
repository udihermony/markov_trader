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


def test_created_strategy_has_trust_label(client):
    headers = _auth_headers(client, "trust@example.com")
    res = client.post("/strategies", json={"name": "SMA", "spec": VALID_SPEC}, headers=headers)
    assert res.json()["trust_label"] == "point_in_time"  # price_bars is point_in_time


def test_preview_valid_spec_returns_stages_without_persisting(client):
    headers = _auth_headers(client, "preview1@example.com")
    res = client.post("/strategies/preview", json={"spec": VALID_SPEC}, headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert body["trust_label"] == "point_in_time"
    kinds = [s["kind"] for s in body["stages"]]
    assert "universe" in kinds
    assert "trigger" in kinds
    for stage in body["stages"]:
        assert stage["description"]  # plain-language sentence present

    # exit/size nodes aren't part of the funnel-narrowing `stages`, but
    # still get a description (VALID_SPEC's x1, x2, s1 nodes)
    assert body["descriptions"]["x1"]
    assert body["descriptions"]["x2"]
    assert body["descriptions"]["s1"]

    # nothing was persisted
    listed = client.get("/strategies", headers=headers).json()
    assert len(listed) == 1  # just the auto-created benchmark strategy


def test_preview_invalid_spec_rejected(client):
    headers = _auth_headers(client, "preview2@example.com")
    res = client.post("/strategies/preview", json={"spec": CYCLIC_SPEC}, headers=headers)
    assert res.status_code == 422


def test_update_strategy_spec(client):
    headers = _auth_headers(client, "update1@example.com")
    created = client.post("/strategies", json={"name": "Original", "spec": VALID_SPEC}, headers=headers).json()

    renamed_spec = {**VALID_SPEC, "name": "renamed"}
    res = client.put(
        f"/strategies/{created['id']}", json={"name": "Updated Name", "spec": renamed_spec}, headers=headers
    )
    assert res.status_code == 200
    body = res.json()
    assert body["name"] == "Updated Name"
    assert body["spec"]["name"] == "renamed"


def test_update_strategy_invalid_spec_rejected(client):
    headers = _auth_headers(client, "update2@example.com")
    created = client.post("/strategies", json={"name": "Original", "spec": VALID_SPEC}, headers=headers).json()

    res = client.put(f"/strategies/{created['id']}", json={"spec": CYCLIC_SPEC}, headers=headers)
    assert res.status_code == 422


def test_update_unowned_strategy_returns_404(client):
    headers_a = _auth_headers(client, "update3a@example.com")
    headers_b = _auth_headers(client, "update3b@example.com")
    created = client.post("/strategies", json={"name": "A's", "spec": VALID_SPEC}, headers=headers_a).json()

    res = client.put(f"/strategies/{created['id']}", json={"name": "Hijacked"}, headers=headers_b)
    assert res.status_code == 404


def test_parent_id_round_trips_through_duplicate(client):
    headers = _auth_headers(client, "dup@example.com")
    original = client.post("/strategies", json={"name": "Original", "spec": VALID_SPEC}, headers=headers).json()
    assert original["parent_id"] is None

    duplicate = client.post(
        "/strategies",
        json={"name": "Original (copy)", "spec": VALID_SPEC, "parent_id": original["id"]},
        headers=headers,
    ).json()
    assert duplicate["parent_id"] == original["id"]


def test_duplicate_with_unowned_parent_returns_404(client):
    headers_a = _auth_headers(client, "dup-a@example.com")
    headers_b = _auth_headers(client, "dup-b@example.com")
    original = client.post("/strategies", json={"name": "A's", "spec": VALID_SPEC}, headers=headers_a).json()

    res = client.post(
        "/strategies",
        json={"name": "Stolen copy", "spec": VALID_SPEC, "parent_id": original["id"]},
        headers=headers_b,
    )
    assert res.status_code == 404


def test_search_counter_and_report_card_start_empty(client):
    headers = _auth_headers(client, "reportcard@example.com")
    strategy = client.post("/strategies", json={"name": "SMA", "spec": VALID_SPEC}, headers=headers).json()

    counter = client.get(f"/strategies/{strategy['id']}/search-counter", headers=headers).json()
    assert counter == {"count": 0, "best_return_pct": None}

    report = client.get(f"/strategies/{strategy['id']}/report-card", headers=headers).json()
    assert report["has_evidence"] is False


def test_calibration_only_counts_graded_ai_experiments(client, db_session):
    from datetime import date

    from sqlalchemy import select

    from backend.db.models import Experiment, User

    headers = _auth_headers(client, "calibration@example.com")
    strategy = client.post("/strategies", json={"name": "SMA", "spec": VALID_SPEC}, headers=headers).json()

    empty = client.get(f"/strategies/{strategy['id']}/calibration", headers=headers).json()
    assert empty == {"predicted": 0, "correct": 0}

    user = db_session.execute(select(User).where(User.email == "calibration@example.com")).scalar_one()
    rows = [
        # AI, graded correct -> counts
        dict(initiated_by="ai", prediction_correct=True),
        # AI, graded wrong -> counts, but not "correct"
        dict(initiated_by="ai", prediction_correct=False),
        # AI, ungraded -> doesn't count
        dict(initiated_by="ai", prediction_correct=None),
        # human, graded -> doesn't count (different kind of record, M7 not M9)
        dict(initiated_by="user", prediction_correct=True),
    ]
    for i, row in enumerate(rows):
        db_session.add(
            Experiment(
                user_id=user.id, strategy_id=strategy["id"], hypothesis=f"h{i}", expected_outcome=f"e{i}",
                actual_outcome="x", period_start=date(2026, 1, 1), period_end=date(2026, 2, 1),
                spec_snapshot_json=VALID_SPEC, result_json={"metrics": {"total_return_pct": 1.0}}, **row,
            )
        )
    db_session.commit()

    calibration = client.get(f"/strategies/{strategy['id']}/calibration", headers=headers).json()
    assert calibration == {"predicted": 2, "correct": 1}
