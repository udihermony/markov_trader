"""Unattended experiment sessions (M9) — DESIGN.md §5.4: "The AI can be
given a goal and left to run." Launching a session (backend/worker/jobs.py)
is the human's one approval for the whole session — unlike the chat surface
(backend/ai/tools.py, M8), where `create_strategy`/`update_strategy` never
persist, the two session-only mutate tools here persist for real:
`create_strategy_variant` / `update_strategy_variant`, both attributed
`created_by='ai'` and lineage-linked (`parent_id`) to the strategy the
session was launched against. Every other tool is the exact same function
`ai/tools.py` already dispatches to. Wallets remain entirely out of reach —
no wallet-mutating tool exists here either.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.ai import tools as ai_tools
from backend.ai.provider import LLMProvider, TokenUsage, get_provider_for_user
from backend.api.routers import strategies
from backend.db.models import Experiment, Strategy, User
from backend.engine.complexity import compute_complexity

MAX_TURNS = 40
REAL_TOOLS = {"run_backtest", "run_neighbourhood_scan", "create_strategy_variant", "update_strategy_variant"}

_REUSED_TOOL_NAMES = {
    "list_strategies", "get_strategy", "validate_strategy", "run_backtest",
    "list_experiments", "get_experiment", "run_luck_test", "run_neighbourhood_scan",
    "list_wallets", "get_wallet", "get_wallet_trades",
}

UNATTENDED_ONLY_TOOL_DEFINITIONS: list[dict] = [
    {
        "name": "create_strategy_variant",
        "description": (
            "Create and save a new strategy variant, branched from the strategy this session is "
            "exploring. This persists immediately — the user already authorized autonomous "
            "exploration by launching this session."
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
        "name": "update_strategy_variant",
        "description": (
            "Overwrite an AI-created variant from this session with a refined spec. Persists immediately."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"strategy_id": {"type": "integer"}, "spec": {"type": "object"}},
            "required": ["strategy_id", "spec"],
        },
    },
    {
        "name": "grade_own_prediction",
        "description": (
            "After seeing a real experiment's actual outcome, judge whether your stated expected "
            "outcome was directionally correct. Call this once for every experiment you run, right "
            "after you see its result — this is what builds your calibration score."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"experiment_id": {"type": "integer"}, "correct": {"type": "boolean"}},
            "required": ["experiment_id", "correct"],
        },
    },
]


def _unattended_tool_definitions() -> list[dict]:
    reused = [d for d in ai_tools.TOOL_DEFINITIONS if d["name"] in _REUSED_TOOL_NAMES]
    return reused + UNATTENDED_ONLY_TOOL_DEFINITIONS


def _system_prompt(root: Strategy, goal: str, budget: int) -> str:
    return f"""You are running an unattended Lab session inside Markov Trader, a swing-trading \
practice app for people who are not professional traders.

Goal: {goal}

You are exploring variants of the strategy "{root.name}" (id {root.id}). You have a hard budget \
of {budget} real experiments for this session — every run_backtest call, every point in a \
run_neighbourhood_scan, and every create_strategy_variant/update_strategy_variant counts against \
it. Budgeted calls made after the budget is spent will be refused.

Rules, non-negotiable:
- create_strategy_variant / update_strategy_variant persist immediately — you don't need (and \
won't get) a human's per-item approval, but every variant must be a real, validated spec, not a \
placeholder.
- Every run_backtest needs a real hypothesis and expected outcome stated beforehand — write your \
prediction before you see the result, not to match it afterward.
- Immediately after each experiment's real result comes back, call grade_own_prediction with \
whether your stated expectation was directionally right. Do this every time, not just when you \
were correct.
- If a tool refuses because this idea has been searched too many times, stop searching that \
lineage and say so in your final digest — do not keep retrying.
- You cannot create, modify, or delete a wallet, approve an order, or spend a holdout unseal — no \
tool for any of that exists here.
- When you are done (budget spent, or you've learned enough), stop calling tools and write a \
short digest: how many experiments you ran, how many were dead ends versus worth attention, and \
the pattern if any — DESIGN.md's own example: "I ran 12 experiments. Ten were dead ends. Two are \
worth your attention. The pattern: every version that helps the hit rate cuts trade count below \
20, so none of them are conclusive yet." Be honest, not promotional.
"""


def _cost(name: str, tool_input: dict) -> int:
    if name == "run_neighbourhood_scan":
        return max(1, len(tool_input.get("values", [])))
    return 1


def _tool_create_strategy_variant(db: Session, user: User, root: Strategy, tool_input: dict) -> dict:
    spec_dict = tool_input["spec"]
    spec = strategies._validate_or_422(spec_dict)  # noqa: SLF001
    graph = strategies._compile_graph(db, spec)  # noqa: SLF001
    variant = Strategy(
        user_id=user.id, name=tool_input.get("name", spec.name), spec_json=spec_dict,
        spec_version=spec.spec_version, parent_id=root.id, created_by="ai",
    )
    db.add(variant)
    db.commit()
    db.refresh(variant)
    complexity = compute_complexity(spec)
    return {
        "id": variant.id, "name": variant.name, "trust_label": graph.trust_label.value,
        "complexity": {"score": complexity.score, "label": complexity.label},
    }


def _tool_update_strategy_variant(db: Session, user: User, tool_input: dict) -> dict:
    variant = strategies._owned_or_404(tool_input["strategy_id"], user, db)  # noqa: SLF001
    spec_dict = tool_input["spec"]
    spec = strategies._validate_or_422(spec_dict)  # noqa: SLF001
    strategies._compile_graph(db, spec)  # noqa: SLF001 — validates it resolves against the registry
    variant.spec_json = spec_dict
    variant.spec_version = spec.spec_version
    db.commit()
    db.refresh(variant)
    return {"id": variant.id, "name": variant.name}


def _tool_grade_own_prediction(db: Session, user: User, tool_input: dict) -> dict:
    experiment = db.execute(
        select(Experiment).where(
            Experiment.id == tool_input["experiment_id"], Experiment.user_id == user.id
        )
    ).scalar_one_or_none()
    if experiment is None:
        return {"error": "experiment not found"}
    experiment.prediction_correct = bool(tool_input["correct"])
    db.commit()
    return {"ok": True}


def _extract_created_ids(name: str, result: Any) -> list[tuple[str, int]]:
    if name in ("create_strategy_variant", "update_strategy_variant"):
        if isinstance(result, dict) and "id" in result:
            return [("strategy", result["id"])]
    elif name == "run_backtest":
        if isinstance(result, dict) and "id" in result:
            return [("experiment", result["id"])]
    elif name == "run_neighbourhood_scan":
        if isinstance(result, list):
            return [("experiment", p["experiment_id"]) for p in result if isinstance(p, dict) and "experiment_id" in p]
    return []


def run_unattended_session(
    db: Session, user: User, root_strategy_id: int, goal: str, budget: int,
    provider: LLMProvider | None = None,
) -> dict:
    root = strategies._owned_or_404(root_strategy_id, user, db)  # noqa: SLF001
    if provider is None:
        provider = get_provider_for_user(db, user)

    system = _system_prompt(root, goal, budget)
    messages: list[dict] = [{"role": "user", "content": f"Goal: {goal}"}]
    tool_defs = _unattended_tool_definitions()

    usage = TokenUsage()
    budget_used = 0
    experiment_ids: list[int] = []
    strategy_ids: list[int] = []
    digest_text: str | None = None

    for _ in range(MAX_TURNS):
        response = provider.complete(messages, tool_defs, system)
        usage = usage + response.usage
        if response.text:
            digest_text = response.text
        if response.stop_reason != "tool_use" or not response.tool_calls:
            break

        messages.append({"role": "assistant", "content": response.raw_content})
        tool_result_blocks = []
        for call in response.tool_calls:
            if call.name in REAL_TOOLS:
                cost = _cost(call.name, call.input)
                if budget_used + cost > budget:
                    result: Any = {
                        "error": f"Session budget of {budget} experiments is exhausted. "
                                 "Stop searching and write your final digest now."
                    }
                else:
                    budget_used += cost
                    if call.name == "create_strategy_variant":
                        result = _tool_create_strategy_variant(db, user, root, call.input)
                    elif call.name == "update_strategy_variant":
                        result = _tool_update_strategy_variant(db, user, call.input)
                    else:
                        result = ai_tools.execute_tool(call.name, call.input, db, user, initiated_by="ai")
                    for kind, created_id in _extract_created_ids(call.name, result):
                        (strategy_ids if kind == "strategy" else experiment_ids).append(created_id)
            elif call.name == "grade_own_prediction":
                result = _tool_grade_own_prediction(db, user, call.input)
            else:
                result = ai_tools.execute_tool(call.name, call.input, db, user, initiated_by="ai")
            tool_result_blocks.append(
                {"type": "tool_result", "tool_use_id": call.id, "content": json.dumps(result)}
            )
        messages.append({"role": "user", "content": tool_result_blocks})

    if digest_text is None:
        messages.append({
            "role": "user",
            "content": (
                "Session ending. Summarize what you did as a short digest: how many experiments, "
                "how many dead ends versus worth attention, and the pattern if any."
            ),
        })
        final = provider.complete(messages, [], system)
        usage = usage + final.usage
        digest_text = final.text or "No digest produced."

    graded = list(
        db.execute(
            select(Experiment.prediction_correct).where(Experiment.id.in_(experiment_ids))
        ).scalars()
    ) if experiment_ids else []
    predicted = sum(1 for g in graded if g is not None)
    correct = sum(1 for g in graded if g is True)

    return {
        "digest": digest_text,
        "experiment_ids": experiment_ids,
        "strategies_created": strategy_ids,
        "tokens": {"input": usage.input_tokens, "output": usage.output_tokens},
        "calibration": {"predicted": predicted, "correct": correct},
    }
