"""Shared Lab (M7) helpers used by both `strategies.py` (search-counter,
report-card) and `experiments.py` (search-counter denominator when persisting
scan points) — kept in `engine/`, not either router, so neither router has to
import from the other and create a cycle.

Lineage-aware counting exists because `strategies.parent_id` lets a user
"Duplicate" a strategy to iterate on it (DESIGN.md §6: "gives lineage, so the
Lab can show how a strategy evolved"). Without walking the whole lineage, a
duplicate would silently reset the search counter to zero — undermining the
luck baseline the counter exists to support.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models import Experiment, Strategy


def lineage_root_id(db: Session, strategy_id: int) -> int:
    current_id = strategy_id
    seen = {current_id}
    while True:
        parent_id = db.execute(
            select(Strategy.parent_id).where(Strategy.id == current_id)
        ).scalar_one_or_none()
        if parent_id is None or parent_id in seen:
            return current_id
        current_id = parent_id
        seen.add(current_id)


def lineage_strategy_ids(db: Session, root_id: int) -> list[int]:
    ids = [root_id]
    frontier = [root_id]
    while frontier:
        children = list(
            db.execute(select(Strategy.id).where(Strategy.parent_id.in_(frontier))).scalars()
        )
        children = [c for c in children if c not in ids]
        ids.extend(children)
        frontier = children
    return ids


def search_counter(db: Session, strategy_id: int) -> tuple[int, float | None]:
    """Non-holdout experiments across a strategy's whole lineage, plus the
    best total_return_pct among them (None if there's nothing to compare
    yet)."""
    root_id = lineage_root_id(db, strategy_id)
    ids = lineage_strategy_ids(db, root_id)
    result_jsons = list(
        db.execute(
            select(Experiment.result_json).where(
                Experiment.strategy_id.in_(ids), Experiment.is_holdout.is_(False)
            )
        ).scalars()
    )
    count = len(result_jsons)
    best: float | None = None
    for r in result_jsons:
        if r and isinstance(r.get("metrics"), dict):
            v = r["metrics"].get("total_return_pct")
            if v is not None:
                best = v if best is None else max(best, v)
    return count, best


def lineage_experiments(db: Session, strategy_id: int) -> list[Experiment]:
    root_id = lineage_root_id(db, strategy_id)
    ids = lineage_strategy_ids(db, root_id)
    return list(
        db.execute(
            select(Experiment).where(Experiment.strategy_id.in_(ids)).order_by(Experiment.created_at)
        ).scalars()
    )
