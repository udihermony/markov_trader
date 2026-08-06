from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.api.deps import get_current_user, get_db
from backend.db.models import Strategy, User
from backend.engine.graph.compiled import CompiledGraph
from backend.engine.graph.registry import get_node_type
from backend.engine.graph.spec import StrategySpec
from backend.engine.graph.validator import GraphValidationError, validate_spec
from backend.sources.finviz_screen import FinvizScreenAdapter, FinvizScreenSource, ScreenerConfig
from backend.sources.price_bars import DataConfig, PriceBarsFeatureAdapter, PriceBarsSource
from backend.sources.registry import SourceRegistry

router = APIRouter(prefix="/strategies", tags=["strategies"])


class CreateStrategyRequest(BaseModel):
    name: str
    spec: dict


class UpdateStrategyRequest(BaseModel):
    name: str | None = None
    spec: dict | None = None


class PreviewRequest(BaseModel):
    spec: dict


class StrategyResponse(BaseModel):
    id: int
    name: str
    spec_version: int
    spec: dict
    trust_label: str
    created_at: datetime


class FunnelStageResponse(BaseModel):
    node_id: str
    kind: str
    type: str
    description: str
    candidates_before: int
    candidates_after: int
    missing_data_count: int


class PreviewResponse(BaseModel):
    stages: list[FunnelStageResponse]
    trust_label: str
    # Every node's plain-language sentence, keyed by node id — `stages`
    # only covers the universe/trigger/confirm/veto narrowing chain
    # (evaluate_funnel's job), but exit/size nodes need a description too
    # and don't have a "candidates survived" count to attach it to.
    descriptions: dict[str, str]


def _validate_or_422(spec_dict: dict) -> StrategySpec:
    try:
        spec = StrategySpec.model_validate(spec_dict)
        validate_spec(spec)
    except (ValidationError, GraphValidationError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    return spec


def _compile_graph(db: Session, spec: StrategySpec) -> CompiledGraph:
    """One fresh registry per call — no global registration pollution
    across requests, the same pattern cli.py/wallet_runner.py already use."""
    price_bars = PriceBarsSource(db, DataConfig())
    screener = FinvizScreenSource(db, ScreenerConfig(), mode="paper")
    registry = SourceRegistry()
    registry.register(PriceBarsFeatureAdapter(price_bars))
    registry.register(FinvizScreenAdapter(screener))
    return CompiledGraph(spec, registry)


def _to_response(strategy: Strategy, db: Session) -> StrategyResponse:
    graph = _compile_graph(db, StrategySpec.model_validate(strategy.spec_json))
    return StrategyResponse(
        id=strategy.id, name=strategy.name, spec_version=strategy.spec_version,
        spec=strategy.spec_json, trust_label=graph.trust_label.value, created_at=strategy.created_at,
    )


@router.post("", response_model=StrategyResponse, status_code=status.HTTP_201_CREATED)
def create_strategy(
    payload: CreateStrategyRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StrategyResponse:
    spec = _validate_or_422(payload.spec)
    strategy = Strategy(
        user_id=user.id, name=payload.name, spec_json=payload.spec, spec_version=spec.spec_version
    )
    db.add(strategy)
    db.commit()
    db.refresh(strategy)
    return _to_response(strategy, db)


@router.post("/preview", response_model=PreviewResponse)
def preview_strategy(
    payload: PreviewRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PreviewResponse:
    """Evaluates an in-progress, possibly-unsaved spec — the M6 funnel
    builder's live counts. Takes the spec directly rather than a strategy
    id since nothing is persisted here."""
    spec = _validate_or_422(payload.spec)
    graph = _compile_graph(db, spec)
    stages = graph.evaluate_funnel(date.today())

    descriptions = {s.node_id: s.description for s in stages}
    for node in spec.nodes:
        if node.id not in descriptions:  # exit/size nodes — not part of the narrowing chain
            descriptions[node.id] = get_node_type(node.type).describe(node.params)

    return PreviewResponse(
        stages=[
            FunnelStageResponse(
                node_id=s.node_id, kind=s.kind, type=s.type, description=s.description,
                candidates_before=s.candidates_before, candidates_after=s.candidates_after,
                missing_data_count=s.missing_data_count,
            )
            for s in stages
        ],
        trust_label=graph.trust_label.value,
        descriptions=descriptions,
    )


@router.get("", response_model=list[StrategyResponse])
def list_strategies(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[StrategyResponse]:
    strategies = db.execute(
        select(Strategy).where(Strategy.user_id == user.id).order_by(Strategy.id)
    ).scalars().all()
    return [_to_response(s, db) for s in strategies]


@router.get("/{strategy_id}", response_model=StrategyResponse)
def get_strategy(
    strategy_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> StrategyResponse:
    strategy = db.execute(
        select(Strategy).where(Strategy.id == strategy_id, Strategy.user_id == user.id)
    ).scalar_one_or_none()
    if strategy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="strategy not found")
    return _to_response(strategy, db)


@router.put("/{strategy_id}", response_model=StrategyResponse)
def update_strategy(
    strategy_id: int,
    payload: UpdateStrategyRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StrategyResponse:
    strategy = db.execute(
        select(Strategy).where(Strategy.id == strategy_id, Strategy.user_id == user.id)
    ).scalar_one_or_none()
    if strategy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="strategy not found")

    if payload.spec is not None:
        spec = _validate_or_422(payload.spec)
        strategy.spec_json = payload.spec
        strategy.spec_version = spec.spec_version
    if payload.name is not None:
        strategy.name = payload.name

    db.commit()
    db.refresh(strategy)
    return _to_response(strategy, db)
