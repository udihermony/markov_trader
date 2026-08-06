from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.api.deps import get_current_user, get_db
from backend.db.models import Strategy, User
from backend.engine.graph.spec import StrategySpec
from backend.engine.graph.validator import GraphValidationError, validate_spec

router = APIRouter(prefix="/strategies", tags=["strategies"])


class CreateStrategyRequest(BaseModel):
    name: str
    spec: dict


class StrategyResponse(BaseModel):
    id: int
    name: str
    spec_version: int
    spec: dict
    created_at: datetime


def _to_response(strategy: Strategy) -> StrategyResponse:
    return StrategyResponse(
        id=strategy.id, name=strategy.name, spec_version=strategy.spec_version,
        spec=strategy.spec_json, created_at=strategy.created_at,
    )


@router.post("", response_model=StrategyResponse, status_code=status.HTTP_201_CREATED)
def create_strategy(
    payload: CreateStrategyRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StrategyResponse:
    try:
        spec = StrategySpec.model_validate(payload.spec)
        validate_spec(spec)
    except (ValidationError, GraphValidationError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc

    strategy = Strategy(
        user_id=user.id, name=payload.name, spec_json=payload.spec, spec_version=spec.spec_version
    )
    db.add(strategy)
    db.commit()
    db.refresh(strategy)
    return _to_response(strategy)


@router.get("", response_model=list[StrategyResponse])
def list_strategies(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[StrategyResponse]:
    strategies = db.execute(
        select(Strategy).where(Strategy.user_id == user.id).order_by(Strategy.id)
    ).scalars().all()
    return [_to_response(s) for s in strategies]


@router.get("/{strategy_id}", response_model=StrategyResponse)
def get_strategy(
    strategy_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> StrategyResponse:
    strategy = db.execute(
        select(Strategy).where(Strategy.id == strategy_id, Strategy.user_id == user.id)
    ).scalar_one_or_none()
    if strategy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="strategy not found")
    return _to_response(strategy)
