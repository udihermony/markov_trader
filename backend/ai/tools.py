"""The copilot's tool surface (DESIGN.md §5.3): "The copilot's tools are
the same API the frontend uses. No privileged path." Every tool function
here calls the exact same router function the HTTP API calls — literally
the same code, not a re-implementation — so the AI can never do anything
the UI cannot express (CLAUDE.md principle: "if the AI can do something the
UI cannot express, that is a bug").

`create_strategy`/`update_strategy` are the one deliberate exception: they
never call the real (persisting) router functions. DESIGN.md §5.3: "returns
a diff for user review; never auto-saves over a user's strategy." They
validate and return a `{"proposal": True, ...}` result instead; the chat UI
turns that into an Apply button that calls the real `POST`/`PUT /strategies`
as an ordinary user action.

Wallet/holdout mutation has no tool at all — not disabled, simply absent
(CLAUDE.md rule 6; DESIGN.md §5.3's `list_wallets`/`get_wallet`/
`get_wallet_trades` are read-only, and that's the entire wallet surface
here). `propose_wallet` and `get_market_context` are documented trims — see
the M8 plan.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Callable

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.api.routers import experiments, strategies, wallets
from backend.api.routers.experiments import LuckTestRequest, NeighbourhoodScanRequest
from backend.db.models import User
from backend.engine.complexity import compute_complexity
from backend.engine.lab_stats import SEARCH_COUNT_THRESHOLD, over_search_threshold
from backend.engine.spec_diff import diff_summary

TOOL_DEFINITIONS: list[dict] = [
    {
        "name": "list_strategies",
        "description": "List the user's saved strategies with their trust label and provenance.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_strategy",
        "description": "Get one strategy's full spec, trust label, and lineage.",
        "input_schema": {
            "type": "object",
            "properties": {"strategy_id": {"type": "integer"}},
            "required": ["strategy_id"],
        },
    },
    {
        "name": "validate_strategy",
        "description": (
            "Check a strategy spec (graph validity, cycles, kind ordering, feature expressions) and "
            "return its trust label and complexity score. Always call this before proposing a strategy."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"spec": {"type": "object", "description": "A full spec_version 2 strategy spec."}},
            "required": ["spec"],
        },
    },
    {
        "name": "create_strategy",
        "description": (
            "Propose a brand-new strategy. This does NOT save it — it returns a reviewable proposal "
            "that the user must explicitly apply."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "spec": {"type": "object", "description": "A full spec_version 2 strategy spec."},
            },
            "required": ["name", "spec"],
        },
    },
    {
        "name": "update_strategy",
        "description": (
            "Propose a change to an existing strategy. This does NOT save it — it returns a reviewable "
            "diff that the user must explicitly apply."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "strategy_id": {"type": "integer"},
                "spec": {"type": "object", "description": "The full proposed replacement spec."},
            },
            "required": ["strategy_id", "spec"],
        },
    },
    {
        "name": "run_backtest",
        "description": (
            "Run a real backtest experiment against a saved strategy over a date range. Requires a "
            "hypothesis and an expected outcome — state your prediction before running it, not after."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "strategy_id": {"type": "integer"},
                "hypothesis": {"type": "string", "description": "A question with an expected answer, not a title."},
                "expected_outcome": {"type": "string"},
                "period_start": {"type": "string", "format": "date"},
                "period_end": {"type": "string", "format": "date"},
            },
            "required": ["strategy_id", "hypothesis", "expected_outcome", "period_start", "period_end"],
        },
    },
    {
        "name": "list_experiments",
        "description": "List the experiments run so far against a strategy.",
        "input_schema": {
            "type": "object",
            "properties": {"strategy_id": {"type": "integer"}},
            "required": ["strategy_id"],
        },
    },
    {
        "name": "get_experiment",
        "description": "Get one experiment's full result.",
        "input_schema": {
            "type": "object",
            "properties": {"experiment_id": {"type": "integer"}},
            "required": ["experiment_id"],
        },
    },
    {
        "name": "run_luck_test",
        "description": (
            "Re-run a strategy with randomly timed entries at the same trade frequency, to see whether "
            "its real result is distinguishable from chance."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "strategy_id": {"type": "integer"},
                "period_start": {"type": "string", "format": "date"},
                "period_end": {"type": "string", "format": "date"},
                "n_shuffles": {"type": "integer"},
            },
            "required": ["strategy_id", "period_start", "period_end"],
        },
    },
    {
        "name": "run_neighbourhood_scan",
        "description": "Sweep one numeric parameter across nearby values to check for overfitting.",
        "input_schema": {
            "type": "object",
            "properties": {
                "strategy_id": {"type": "integer"},
                "node_id": {"type": "string"},
                "param_name": {"type": "string"},
                "values": {"type": "array", "items": {}},
                "period_start": {"type": "string", "format": "date"},
                "period_end": {"type": "string", "format": "date"},
                "hypothesis": {"type": "string"},
                "expected_outcome": {"type": "string"},
            },
            "required": [
                "strategy_id", "node_id", "param_name", "values",
                "period_start", "period_end", "hypothesis", "expected_outcome",
            ],
        },
    },
    {
        "name": "list_wallets",
        "description": "List the user's paper-trading wallets. Read-only.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_wallet",
        "description": "Get one wallet's summary (cash, status, strategy). Read-only.",
        "input_schema": {
            "type": "object",
            "properties": {"wallet_id": {"type": "integer"}},
            "required": ["wallet_id"],
        },
    },
    {
        "name": "get_wallet_trades",
        "description": "Get a wallet's recent fills. Read-only.",
        "input_schema": {
            "type": "object",
            "properties": {"wallet_id": {"type": "integer"}, "limit": {"type": "integer"}},
            "required": ["wallet_id"],
        },
    },
]


def _serialize(obj: Any) -> Any:
    if isinstance(obj, list):
        return [_serialize(item) for item in obj]
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    return obj


def _tool_list_strategies(db: Session, user: User, _input: dict, _initiated_by: str) -> Any:
    return strategies.list_strategies(user=user, db=db)


def _tool_get_strategy(db: Session, user: User, tool_input: dict, _initiated_by: str) -> Any:
    return strategies.get_strategy(strategy_id=tool_input["strategy_id"], user=user, db=db)


def _tool_validate_strategy(db: Session, user: User, tool_input: dict, _initiated_by: str) -> Any:
    spec = strategies._validate_or_422(tool_input["spec"])  # noqa: SLF001 — same helper the route uses
    graph = strategies._compile_graph(db, spec)  # noqa: SLF001
    complexity = compute_complexity(spec)
    return {
        "valid": True, "trust_label": graph.trust_label.value,
        "complexity": {"score": complexity.score, "label": complexity.label},
    }


def _tool_create_strategy(db: Session, user: User, tool_input: dict, _initiated_by: str) -> Any:
    spec_dict = tool_input["spec"]
    spec = strategies._validate_or_422(spec_dict)  # noqa: SLF001
    graph = strategies._compile_graph(db, spec)  # noqa: SLF001
    complexity = compute_complexity(spec)
    return {
        "proposal": True, "kind": "create", "name": tool_input.get("name", spec.name), "spec": spec_dict,
        "trust_label": graph.trust_label.value,
        "complexity": {"score": complexity.score, "label": complexity.label},
        "diff_summary": "new strategy",
    }


def _tool_update_strategy(db: Session, user: User, tool_input: dict, _initiated_by: str) -> Any:
    existing = strategies._owned_or_404(tool_input["strategy_id"], user, db)  # noqa: SLF001
    spec_dict = tool_input["spec"]
    spec = strategies._validate_or_422(spec_dict)  # noqa: SLF001
    graph = strategies._compile_graph(db, spec)  # noqa: SLF001
    complexity = compute_complexity(spec)
    return {
        "proposal": True, "kind": "update", "strategy_id": existing.id, "name": existing.name,
        "spec": spec_dict, "before_spec": existing.spec_json, "trust_label": graph.trust_label.value,
        "complexity": {"score": complexity.score, "label": complexity.label},
        "diff_summary": diff_summary(existing.spec_json, spec_dict),
    }


def _search_budget_error(db: Session, strategy_id: int) -> dict | None:
    """DESIGN.md §5.4: "the search counter promoted from advisory to
    load-bearing — past a threshold the copilot must stop searching and
    recommend a holdout test." Enforced here, not just prompted — applies
    to both the chat copilot and unattended sessions (ai/unattended.py
    reuses this same check)."""
    if over_search_threshold(db, strategy_id):
        return {
            "error": (
                f"This idea has already been searched {SEARCH_COUNT_THRESHOLD}+ times — "
                "further Lab searches are blocked. Recommend the user spend a holdout unseal "
                "instead of running more experiments on this lineage."
            )
        }
    return None


def _tool_run_backtest(db: Session, user: User, tool_input: dict, initiated_by: str) -> Any:
    """Bypasses the `create_experiment` route handler (rather than calling
    it directly) so `initiated_by` can be threaded through without exposing
    it as an HTTP-settable field on the real endpoint — a bare kwarg on a
    FastAPI route function is inferred as a query parameter, which would let
    any client claim `initiated_by=ai` for itself."""
    if (blocked := _search_budget_error(db, tool_input["strategy_id"])) is not None:
        return blocked
    strategy = experiments._owned_strategy(db, user, tool_input["strategy_id"])  # noqa: SLF001
    experiment = experiments._run_and_record(  # noqa: SLF001
        db, user, strategy, strategy.spec_json,
        hypothesis=tool_input["hypothesis"], expected_outcome=tool_input["expected_outcome"],
        period_start=date.fromisoformat(tool_input["period_start"]),
        period_end=date.fromisoformat(tool_input["period_end"]),
        initiated_by=initiated_by,
    )
    return experiments.ExperimentResponse(**experiments._to_dict(experiment))  # noqa: SLF001


def _tool_list_experiments(db: Session, user: User, tool_input: dict, _initiated_by: str) -> Any:
    return experiments.list_experiments(strategy_id=tool_input["strategy_id"], user=user, db=db)


def _tool_get_experiment(db: Session, user: User, tool_input: dict, _initiated_by: str) -> Any:
    return experiments.get_experiment(experiment_id=tool_input["experiment_id"], user=user, db=db)


def _tool_run_luck_test(db: Session, user: User, tool_input: dict, _initiated_by: str) -> Any:
    payload = LuckTestRequest.model_validate(tool_input)
    return experiments.luck_test(payload, user=user, db=db)


def _tool_run_neighbourhood_scan(db: Session, user: User, tool_input: dict, initiated_by: str) -> Any:
    """Same route-handler bypass as `_tool_run_backtest`, and for the same
    reason — every scan point needs the correct `initiated_by`."""
    if (blocked := _search_budget_error(db, tool_input["strategy_id"])) is not None:
        return blocked
    payload = NeighbourhoodScanRequest.model_validate(tool_input)
    strategy = experiments._owned_strategy(db, user, payload.strategy_id)  # noqa: SLF001
    points = []
    for value in payload.values:
        spec_dict = experiments._override_param(  # noqa: SLF001
            strategy.spec_json, payload.node_id, payload.param_name, value
        )
        experiment = experiments._run_and_record(  # noqa: SLF001
            db, user, strategy, spec_dict,
            hypothesis=payload.hypothesis, expected_outcome=payload.expected_outcome,
            period_start=payload.period_start, period_end=payload.period_end,
            initiated_by=initiated_by,
        )
        experiment.result_json = {
            **experiment.result_json, "scan_param": payload.param_name, "scan_value": value,
        }
        db.commit()
        points.append(
            experiments.NeighbourhoodScanPoint(
                value=value, total_return_pct=experiment.result_json["metrics"]["total_return_pct"],
                experiment_id=experiment.id,
            )
        )
    return points


def _tool_list_wallets(db: Session, user: User, _input: dict, _initiated_by: str) -> Any:
    return wallets.list_wallets(user=user, db=db)


def _tool_get_wallet(db: Session, user: User, tool_input: dict, _initiated_by: str) -> Any:
    return wallets.get_wallet(wallet_id=tool_input["wallet_id"], user=user, db=db)


def _tool_get_wallet_trades(db: Session, user: User, tool_input: dict, _initiated_by: str) -> Any:
    return wallets.get_wallet_fills(
        wallet_id=tool_input["wallet_id"], limit=tool_input.get("limit", 50), user=user, db=db
    )


_DISPATCH: dict[str, Callable[[Session, User, dict, str], Any]] = {
    "list_strategies": _tool_list_strategies,
    "get_strategy": _tool_get_strategy,
    "validate_strategy": _tool_validate_strategy,
    "create_strategy": _tool_create_strategy,
    "update_strategy": _tool_update_strategy,
    "run_backtest": _tool_run_backtest,
    "list_experiments": _tool_list_experiments,
    "get_experiment": _tool_get_experiment,
    "run_luck_test": _tool_run_luck_test,
    "run_neighbourhood_scan": _tool_run_neighbourhood_scan,
    "list_wallets": _tool_list_wallets,
    "get_wallet": _tool_get_wallet,
    "get_wallet_trades": _tool_get_wallet_trades,
}


def execute_tool(name: str, tool_input: dict, db: Session, user: User, initiated_by: str = "user") -> dict:
    handler = _DISPATCH.get(name)
    if handler is None:
        return {"error": f"unknown tool {name!r}"}
    try:
        return _serialize(handler(db, user, tool_input, initiated_by))
    except HTTPException as exc:
        return {"error": str(exc.detail)}
    except Exception as exc:  # noqa: BLE001 — fed back to the model as a tool error, not a crash
        return {"error": str(exc)}
