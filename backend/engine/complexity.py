"""Direct Python port of frontend/src/lib/complexity.ts's formula — needed
here because the copilot's `create_strategy`/`update_strategy` tool results
(backend/ai/tools.py) can't reach into frontend TS. Keep the two in sync by
hand; the formula is small and stable (DESIGN.md §3: "more nodes and more
parameters means more overfitting risk" — a simple, honest count, not a
real holdout gate)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from backend.engine.graph.spec import StrategySpec

ComplexityLabel = Literal["Low", "Medium", "High"]


@dataclass(frozen=True)
class Complexity:
    score: int
    label: ComplexityLabel


def compute_complexity(spec: StrategySpec) -> Complexity:
    score = sum(1 + len(node.params) for node in spec.nodes)
    label: ComplexityLabel = "Low" if score < 8 else "Medium" if score < 15 else "High"
    return Complexity(score=score, label=label)
