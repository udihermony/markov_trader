from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.api.deps import get_db
from backend.api.main import app
from backend.db.models import Order, SkippedSignal, User, Wallet


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


def _auth_headers(client, email="orders-user@example.com") -> dict:
    client.post("/auth/register", json={"email": email, "password": "hunter2pw"})
    token = client.post("/auth/login", json={"email": email, "password": "hunter2pw"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _seed_order(db_session, client, email, *, status="pending", action="BUY") -> Order:
    user = db_session.execute(select(User).where(User.email == email)).scalar_one()
    wallet = db_session.execute(select(Wallet).where(Wallet.user_id == user.id)).scalars().first()
    order = Order(
        wallet_id=wallet.id, created_date=date.today(), ticker="AAPL", action=action,
        cash_amount=1000.0, reason="test_reason", status=status,
    )
    db_session.add(order)
    db_session.flush()
    return order


def test_list_orders_scoped_to_owner(client, db_session):
    headers_a = _auth_headers(client, "oa@example.com")
    _auth_headers(client, "ob@example.com")
    _seed_order(db_session, client, "oa@example.com")

    res_a = client.get("/orders?status=pending", headers=headers_a)
    assert len(res_a.json()) == 1

    res_b = client.get("/orders?status=pending", headers=_auth_headers(client, "ob@example.com"))
    assert len(res_b.json()) == 0


def test_decision_approve_records_without_cancelling(client, db_session):
    headers = _auth_headers(client, "oc@example.com")
    order = _seed_order(db_session, client, "oc@example.com")

    res = client.post(f"/orders/{order.id}/decision", json={"decision": "approve"}, headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert body["user_decision"] == "approve"
    assert body["status"] == "pending"  # unchanged — approve doesn't execute or cancel anything


def test_decision_skip_cancels_and_logs_skipped_signal(client, db_session):
    headers = _auth_headers(client, "od@example.com")
    order = _seed_order(db_session, client, "od@example.com")

    res = client.post(f"/orders/{order.id}/decision", json={"decision": "skip"}, headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert body["user_decision"] == "skip"
    assert body["status"] == "cancelled"

    skipped = db_session.execute(
        select(SkippedSignal).where(SkippedSignal.wallet_id == order.wallet_id)
    ).scalars().all()
    assert len(skipped) == 1
    assert skipped[0].stage == "user_skip"
    assert skipped[0].metadata_json["order_id"] == order.id


def test_decision_on_non_pending_order_rejected(client, db_session):
    headers = _auth_headers(client, "oe@example.com")
    order = _seed_order(db_session, client, "oe@example.com", status="executed")

    res = client.post(f"/orders/{order.id}/decision", json={"decision": "approve"}, headers=headers)
    assert res.status_code == 400


def test_decision_on_unowned_order_returns_404(client, db_session):
    _auth_headers(client, "of@example.com")
    order = _seed_order(db_session, client, "of@example.com")
    headers_b = _auth_headers(client, "og@example.com")

    res = client.post(f"/orders/{order.id}/decision", json={"decision": "skip"}, headers=headers_b)
    assert res.status_code == 404
