from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.api.deps import get_current_user, get_db
from backend.db.models import Experiment, Strategy, User
from backend.engine.graph.compiled import CompiledGraph
from backend.engine.graph.registry import get_node_type
from backend.engine.graph.spec import StrategySpec
from backend.engine.graph.validator import GraphValidationError, validate_spec
from backend.engine.lab_stats import lineage_experiments, search_counter as _search_counter
from backend.sources.finviz_screen import FinvizScreenAdapter, FinvizScreenSource, ScreenerConfig
from backend.sources.price_bars import DataConfig, PriceBarsFeatureAdapter, PriceBarsSource
from backend.sources.registry import SourceRegistry

router = APIRouter(prefix="/strategies", tags=["strategies"])


class CreateStrategyRequest(BaseModel):
    name: str
    spec: dict
    parent_id: int | None = None  # set by "Duplicate strategy" for Lab lineage (DESIGN.md §6)


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
    parent_id: int | None
    created_by: str
    created_at: datetime


class QuestionAnswer(BaseModel):
    answer: str
    detail: str


class ReportCardResponse(BaseModel):
    has_evidence: bool
    evidence_source: str | None = None
    beat_doing_nothing: QuestionAnswer | None = None
    real_or_luck: QuestionAnswer | None = None
    how_often_right: QuestionAnswer | None = None
    could_stomach_it: QuestionAnswer | None = None


class SearchCounterResponse(BaseModel):
    count: int
    best_return_pct: float | None


class CalibrationResponse(BaseModel):
    predicted: int
    correct: int


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
        spec=strategy.spec_json, trust_label=graph.trust_label.value, parent_id=strategy.parent_id,
        created_by=strategy.created_by, created_at=strategy.created_at,
    )


@router.post("", response_model=StrategyResponse, status_code=status.HTTP_201_CREATED)
def create_strategy(
    payload: CreateStrategyRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StrategyResponse:
    spec = _validate_or_422(payload.spec)
    if payload.parent_id is not None:
        parent = db.execute(
            select(Strategy).where(Strategy.id == payload.parent_id, Strategy.user_id == user.id)
        ).scalar_one_or_none()
        if parent is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="parent strategy not found")
    strategy = Strategy(
        user_id=user.id, name=payload.name, spec_json=payload.spec, spec_version=spec.spec_version,
        parent_id=payload.parent_id,
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


def _owned_or_404(strategy_id: int, user: User, db: Session) -> Strategy:
    strategy = db.execute(
        select(Strategy).where(Strategy.id == strategy_id, Strategy.user_id == user.id)
    ).scalar_one_or_none()
    if strategy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="strategy not found")
    return strategy


@router.get("/{strategy_id}/search-counter", response_model=SearchCounterResponse)
def get_search_counter(
    strategy_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> SearchCounterResponse:
    _owned_or_404(strategy_id, user, db)
    count, best = _search_counter(db, strategy_id)
    return SearchCounterResponse(count=count, best_return_pct=best)


@router.get("/{strategy_id}/report-card", response_model=ReportCardResponse)
def get_report_card(
    strategy_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> ReportCardResponse:
    """DESIGN.md §3.5's four questions. Prefers a holdout result over any
    Lab result — the evidence pipeline (§2) ranks holdout evidence above
    search-contaminated Lab evidence — and is explicit about "no evidence
    yet" rather than fabricating an answer from nothing."""
    _owned_or_404(strategy_id, user, db)
    experiments = [e for e in lineage_experiments(db, strategy_id) if e.user_id == user.id and e.result_json]
    if not experiments:
        return ReportCardResponse(has_evidence=False)

    holdout_experiments = [e for e in experiments if e.is_holdout]
    if holdout_experiments:
        reference = max(holdout_experiments, key=lambda e: e.created_at)
        evidence_source = "holdout"
    else:
        reference = max(experiments, key=lambda e: e.result_json["metrics"]["total_return_pct"])
        evidence_source = "lab"
    m = reference.result_json["metrics"]
    count, _ = _search_counter(db, strategy_id)

    if m.get("benchmark_return_pct") is not None:
        beat_doing_nothing = QuestionAnswer(
            answer="Yes" if m["total_return_pct"] > m["benchmark_return_pct"] else "No",
            detail=f"{m['total_return_pct']:+.1f}% vs. SPY's {m['benchmark_return_pct']:+.1f}% over the same period.",
        )
    else:
        beat_doing_nothing = QuestionAnswer(
            answer=f"{m['total_return_pct']:+.1f}%", detail="No benchmark data available for this period.",
        )

    if evidence_source == "holdout":
        real_or_luck = QuestionAnswer(
            answer="This is your one honest look",
            detail="From your sealed holdout period — the only result in this app that isn't contaminated by search.",
        )
    else:
        real_or_luck = QuestionAnswer(
            answer="Unproven — Lab only",
            detail=(
                f"{count} Lab experiment{'s' if count != 1 else ''} run so far on this idea. "
                "Lab results are contaminated by search; run a luck test or spend a holdout unseal for a cleaner answer."
            ),
        )

    if m.get("hit_rate") is not None:
        how_often_right = QuestionAnswer(
            answer=f"{m['hit_rate']:.0f}%",
            detail=f"{m['n_closed_trades']} of {m['n_trades']} fills were closed round trips.",
        )
    else:
        how_often_right = QuestionAnswer(answer="Not enough trades yet", detail="No closed round trips to grade.")

    could_stomach_it = QuestionAnswer(
        answer=f"Worst stretch: -{m['max_drawdown_pct']:.1f}%",
        detail="The largest drop from a peak to a later low in this run's equity curve.",
    )

    return ReportCardResponse(
        has_evidence=True, evidence_source=evidence_source,
        beat_doing_nothing=beat_doing_nothing, real_or_luck=real_or_luck,
        how_often_right=how_often_right, could_stomach_it=could_stomach_it,
    )


@router.get("/{strategy_id}/calibration", response_model=CalibrationResponse)
def get_calibration(
    strategy_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> CalibrationResponse:
    """DESIGN.md §5.4: "producing a calibration score for the copilot
    itself: 'Claude predicted 12 outcomes and got 4 right.'" Only AI-run
    experiments count — a human's own Yes/No self-judgment (M7) is a
    different kind of record, not the AI grading itself (M9)."""
    _owned_or_404(strategy_id, user, db)
    ai_experiments = [
        e for e in lineage_experiments(db, strategy_id)
        if e.user_id == user.id and e.initiated_by == "ai" and e.prediction_correct is not None
    ]
    predicted = len(ai_experiments)
    correct = sum(1 for e in ai_experiments if e.prediction_correct is True)
    return CalibrationResponse(predicted=predicted, correct=correct)
