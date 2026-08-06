"""Sealed holdout (M7) — DESIGN.md §3/§4.2: one honest shot per user, spent
deliberately. Per-user, not per-strategy (confirmed with the user against
DESIGN.md §10.6's own open question) — the `UNIQUE(user_id)` constraint on
`holdouts` is what makes "sealed once" structural rather than convention.
"""
from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.api.deps import get_current_user, get_db
from backend.api.routers.experiments import ExperimentResponse, _owned_strategy, _run_and_record, _to_dict
from backend.api.routers.strategies import _compile_graph
from backend.db.models import Holdout, User
from backend.engine.graph.spec import StrategySpec
from backend.sources.registry import TrustClass

router = APIRouter(prefix="/holdouts", tags=["holdouts"])


class CreateHoldoutRequest(BaseModel):
    start_date: date
    end_date: date
    unseals_total: int = Field(default=3, ge=1, le=10)


class HoldoutResponse(BaseModel):
    id: int
    start_date: date
    end_date: date
    unseals_total: int
    unseals_used: int
    created_at: datetime


class UnsealRequest(BaseModel):
    strategy_id: int
    hypothesis: str = Field(min_length=1)
    expected_outcome: str = Field(min_length=1)


def _to_response(h: Holdout) -> HoldoutResponse:
    return HoldoutResponse(
        id=h.id, start_date=h.start_date, end_date=h.end_date,
        unseals_total=h.unseals_total, unseals_used=h.unseals_used, created_at=h.created_at,
    )


@router.post("", response_model=HoldoutResponse, status_code=status.HTTP_201_CREATED)
def seal_holdout(
    payload: CreateHoldoutRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> HoldoutResponse:
    if payload.start_date >= payload.end_date:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="start_date must be before end_date")
    if payload.end_date >= date.today():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="a holdout must be a period that has already elapsed",
        )
    existing = db.execute(select(Holdout).where(Holdout.user_id == user.id)).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="a holdout is already sealed for this account")

    holdout = Holdout(
        user_id=user.id, start_date=payload.start_date, end_date=payload.end_date,
        unseals_total=payload.unseals_total,
    )
    db.add(holdout)
    db.commit()
    db.refresh(holdout)
    return _to_response(holdout)


@router.get("", response_model=HoldoutResponse | None)
def get_holdout(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> HoldoutResponse | None:
    holdout = db.execute(select(Holdout).where(Holdout.user_id == user.id)).scalar_one_or_none()
    return _to_response(holdout) if holdout is not None else None


@router.post("/{holdout_id}/unseal")
def unseal(
    holdout_id: int,
    payload: UnsealRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    holdout = db.execute(
        select(Holdout).where(Holdout.id == holdout_id, Holdout.user_id == user.id)
    ).scalar_one_or_none()
    if holdout is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="holdout not found")
    if holdout.unseals_used >= holdout.unseals_total:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="no unseals remaining")

    strategy = _owned_strategy(db, user, payload.strategy_id)
    graph = _compile_graph(db, StrategySpec.model_validate(strategy.spec_json))
    if graph.trust_label == TrustClass.LIVE_ONLY:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="a strategy with a live-only source can never satisfy a holdout unseal",
        )

    experiment = _run_and_record(
        db, user, strategy, strategy.spec_json,
        hypothesis=payload.hypothesis, expected_outcome=payload.expected_outcome,
        period_start=holdout.start_date, period_end=holdout.end_date, is_holdout=True,
    )
    holdout.unseals_used += 1
    db.commit()
    db.refresh(holdout)

    return {"holdout": _to_response(holdout), "experiment": ExperimentResponse(**_to_dict(experiment))}
