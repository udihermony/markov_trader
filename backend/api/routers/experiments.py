"""Lab experiments (M7) — DESIGN.md §3: "Every experiment requires a
hypothesis before it runs... a question with an expected answer, not a
title." Every run here is a real, ephemeral backtest (backend/engine/
backtest_runner.py) — no wallet-shaped row survives it (CLAUDE.md rule 7).
"""
from __future__ import annotations

import copy
import random
from dataclasses import asdict
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.api.deps import get_current_user, get_db
from backend.api.routers.strategies import _validate_or_422
from backend.db.models import Experiment, Strategy, User
from backend.engine.backtest_runner import calibrated_entry_probability, run_ephemeral_backtest
from backend.engine.spec_diff import diff_summary as _diff_summary

router = APIRouter(prefix="/experiments", tags=["experiments"])


def _owned_strategy(db: Session, user: User, strategy_id: int) -> Strategy:
    strategy = db.execute(
        select(Strategy).where(Strategy.id == strategy_id, Strategy.user_id == user.id)
    ).scalar_one_or_none()
    if strategy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="strategy not found")
    return strategy


def _compose_actual_outcome(metrics: dict) -> str:
    parts = [f"Returned {metrics['total_return_pct']:+.1f}%"]
    if metrics.get("benchmark_return_pct") is not None:
        parts[0] += f" vs SPY's {metrics['benchmark_return_pct']:+.1f}%"
    parts.append(f"{metrics['n_trades']} trades")
    if metrics.get("hit_rate") is not None:
        parts.append(f"{metrics['hit_rate']:.0f}% hit rate")
    parts.append(f"max drawdown {metrics['max_drawdown_pct']:.1f}%")
    return ", ".join(parts) + "."


def _override_param(spec_dict: dict, node_id: str, param_name: str, value: object) -> dict:
    spec_copy = copy.deepcopy(spec_dict)
    for node in spec_copy.get("nodes", []):
        if node["id"] == node_id:
            node["params"][param_name] = value
            return spec_copy
    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"node {node_id!r} not found")


class CreateExperimentRequest(BaseModel):
    strategy_id: int
    hypothesis: str = Field(min_length=1)
    expected_outcome: str = Field(min_length=1)
    period_start: date
    period_end: date


class ExperimentResponse(BaseModel):
    id: int
    strategy_id: int
    hypothesis: str
    expected_outcome: str
    actual_outcome: str | None
    prediction_correct: bool | None
    period_start: date
    period_end: date
    initiated_by: str
    is_holdout: bool
    diff_summary: str | None = None
    result_json: dict | None
    created_at: datetime


class PredictionCorrectRequest(BaseModel):
    correct: bool


class NeighbourhoodScanRequest(BaseModel):
    strategy_id: int
    node_id: str
    param_name: str
    values: list[float | str]
    period_start: date
    period_end: date
    hypothesis: str = Field(min_length=1)
    expected_outcome: str = Field(min_length=1)


class NeighbourhoodScanPoint(BaseModel):
    value: float | str
    total_return_pct: float
    experiment_id: int


class LuckTestRequest(BaseModel):
    strategy_id: int
    period_start: date
    period_end: date
    n_shuffles: int = Field(default=30, ge=1, le=200)


class LuckTestResponse(BaseModel):
    real_return_pct: float
    shuffled_returns: list[float]
    percentile: float


def _run_and_record(
    db: Session, user: User, strategy: Strategy, spec_dict: dict, *,
    hypothesis: str, expected_outcome: str, period_start: date, period_end: date,
    is_holdout: bool = False, initiated_by: str = "user",
) -> Experiment:
    spec = _validate_or_422(spec_dict)
    # Reuse the request's own connection — see backtest_runner.run_ephemeral_backtest's
    # `connection` docstring: a fresh connection can't see this request's own
    # uncommitted work, and reusing it keeps the rollback scoped to a SAVEPOINT
    # nested inside the request's transaction rather than opening a second one.
    result = run_ephemeral_backtest(spec, period_start, period_end, connection=db.connection())
    metrics = asdict(result.metrics)
    experiment = Experiment(
        user_id=user.id, strategy_id=strategy.id, hypothesis=hypothesis,
        expected_outcome=expected_outcome, actual_outcome=_compose_actual_outcome(metrics),
        period_start=period_start, period_end=period_end, is_holdout=is_holdout,
        spec_snapshot_json=spec_dict, initiated_by=initiated_by,
        result_json={"metrics": metrics, "equity_curve": result.equity_curve, "fills": result.fills},
    )
    db.add(experiment)
    db.commit()
    db.refresh(experiment)
    return experiment


@router.post("", response_model=ExperimentResponse, status_code=status.HTTP_201_CREATED)
def create_experiment(
    payload: CreateExperimentRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ExperimentResponse:
    strategy = _owned_strategy(db, user, payload.strategy_id)
    experiment = _run_and_record(
        db, user, strategy, strategy.spec_json,
        hypothesis=payload.hypothesis, expected_outcome=payload.expected_outcome,
        period_start=payload.period_start, period_end=payload.period_end,
    )
    return ExperimentResponse(**_to_dict(experiment))


def _to_dict(e: Experiment, diff_summary: str | None = None) -> dict:
    return dict(
        id=e.id, strategy_id=e.strategy_id, hypothesis=e.hypothesis,
        expected_outcome=e.expected_outcome, actual_outcome=e.actual_outcome,
        prediction_correct=e.prediction_correct, period_start=e.period_start,
        period_end=e.period_end, initiated_by=e.initiated_by, is_holdout=e.is_holdout,
        diff_summary=diff_summary, result_json=e.result_json, created_at=e.created_at,
    )


@router.get("", response_model=list[ExperimentResponse])
def list_experiments(
    strategy_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[ExperimentResponse]:
    _owned_strategy(db, user, strategy_id)  # 404s if not owned
    rows = list(
        db.execute(
            select(Experiment)
            .where(Experiment.strategy_id == strategy_id, Experiment.user_id == user.id)
            .order_by(Experiment.created_at)
        ).scalars()
    )
    out: list[ExperimentResponse] = []
    prev_spec: dict | None = None
    for e in rows:
        out.append(ExperimentResponse(**_to_dict(e, diff_summary=_diff_summary(prev_spec, e.spec_snapshot_json))))
        prev_spec = e.spec_snapshot_json
    return out


@router.get("/{experiment_id}", response_model=ExperimentResponse)
def get_experiment(
    experiment_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> ExperimentResponse:
    e = db.execute(
        select(Experiment).where(Experiment.id == experiment_id, Experiment.user_id == user.id)
    ).scalar_one_or_none()
    if e is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="experiment not found")
    return ExperimentResponse(**_to_dict(e))


@router.post("/{experiment_id}/prediction-correct", response_model=ExperimentResponse)
def set_prediction_correct(
    experiment_id: int,
    payload: PredictionCorrectRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ExperimentResponse:
    e = db.execute(
        select(Experiment).where(Experiment.id == experiment_id, Experiment.user_id == user.id)
    ).scalar_one_or_none()
    if e is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="experiment not found")
    e.prediction_correct = payload.correct
    db.commit()
    db.refresh(e)
    return ExperimentResponse(**_to_dict(e))


@router.post("/neighbourhood-scan", response_model=list[NeighbourhoodScanPoint])
def neighbourhood_scan(
    payload: NeighbourhoodScanRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[NeighbourhoodScanPoint]:
    """Every point is a real search over a variant, so every point is
    persisted as its own Experiment (design decision #3 — otherwise the
    search counter is trivially gameable via a scan)."""
    strategy = _owned_strategy(db, user, payload.strategy_id)
    points: list[NeighbourhoodScanPoint] = []
    for value in payload.values:
        spec_dict = _override_param(strategy.spec_json, payload.node_id, payload.param_name, value)
        experiment = _run_and_record(
            db, user, strategy, spec_dict,
            hypothesis=payload.hypothesis, expected_outcome=payload.expected_outcome,
            period_start=payload.period_start, period_end=payload.period_end,
        )
        experiment.result_json = {
            **experiment.result_json, "scan_param": payload.param_name, "scan_value": value,
        }
        db.commit()
        points.append(
            NeighbourhoodScanPoint(
                value=value, total_return_pct=experiment.result_json["metrics"]["total_return_pct"],
                experiment_id=experiment.id,
            )
        )
    return points


@router.post("/luck-test", response_model=LuckTestResponse)
def luck_test(
    payload: LuckTestRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LuckTestResponse:
    """Validates a single already-chosen result's robustness — not a search
    for a better one — so nothing here is persisted as an Experiment and the
    search counter doesn't move (design decision #3)."""
    strategy = _owned_strategy(db, user, payload.strategy_id)
    spec = _validate_or_422(strategy.spec_json)

    connection = db.connection()
    real = run_ephemeral_backtest(spec, payload.period_start, payload.period_end, connection=connection)
    n_trading_days = len(real.equity_curve)
    p = calibrated_entry_probability(real.metrics.n_trades, n_trading_days)

    rng = random.Random()
    shuffled_returns: list[float] = []
    for _ in range(payload.n_shuffles):
        shuffled = run_ephemeral_backtest(
            spec, payload.period_start, payload.period_end,
            entry_randomizer=lambda: rng.random() < p, connection=connection,
        )
        shuffled_returns.append(shuffled.metrics.total_return_pct)

    real_return = real.metrics.total_return_pct
    percentile = (
        sum(1 for x in shuffled_returns if x <= real_return) / len(shuffled_returns) * 100
        if shuffled_returns else 0.0
    )
    return LuckTestResponse(
        real_return_pct=real_return, shuffled_returns=shuffled_returns, percentile=percentile
    )
