from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.api.deps import get_current_user, get_db
from backend.db.models import Order, SkippedSignal, User, Wallet

router = APIRouter(prefix="/orders", tags=["orders"])


class OrderResponse(BaseModel):
    id: int
    wallet_id: int
    created_date: date
    ticker: str
    action: str
    cash_amount: float | None
    reason: str
    status: str
    user_decision: str | None


class OrderDecisionRequest(BaseModel):
    decision: Literal["approve", "skip"]


def _to_response(order: Order) -> OrderResponse:
    return OrderResponse(
        id=order.id, wallet_id=order.wallet_id, created_date=order.created_date,
        ticker=order.ticker, action=order.action,
        cash_amount=float(order.cash_amount) if order.cash_amount is not None else None,
        reason=order.reason, status=order.status, user_decision=order.user_decision,
    )


@router.get("", response_model=list[OrderResponse])
def list_orders(
    status_filter: str | None = Query(None, alias="status"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[OrderResponse]:
    stmt = select(Order).join(Wallet, Order.wallet_id == Wallet.id).where(Wallet.user_id == user.id)
    if status_filter:
        stmt = stmt.where(Order.status == status_filter)
    orders = db.execute(stmt.order_by(Order.id)).scalars().all()
    return [_to_response(o) for o in orders]


@router.post("/{order_id}/decision", response_model=OrderResponse)
def decide_order(
    order_id: int,
    payload: OrderDecisionRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OrderResponse:
    order = db.execute(
        select(Order).join(Wallet, Order.wallet_id == Wallet.id)
        .where(Order.id == order_id, Wallet.user_id == user.id)
    ).scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="order not found")
    if order.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"order is no longer pending (status={order.status})",
        )

    order.user_decision = payload.decision
    if payload.decision == "skip":
        order.status = "cancelled"
        db.add(
            SkippedSignal(
                wallet_id=order.wallet_id, date=order.created_date, ticker=order.ticker,
                stage="user_skip", reason="user_skip", metadata_json={"order_id": order.id},
            )
        )
    db.commit()
    db.refresh(order)
    return _to_response(order)
