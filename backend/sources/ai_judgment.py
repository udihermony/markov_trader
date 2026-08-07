"""The `ai_judgment` source (M10) — what `ai_news_check`/`ai_regime_check`
veto nodes read from (backend/engine/graph/nodes.py). DESIGN.md §5.2's
"look-ahead problem with AI nodes": an LLM asked about a past date may
simply remember what happened, so an AI judgment can never be honestly
replayed in a backtest. This source is `trust_class=LIVE_ONLY` — any
strategy that registers it is permanently marked forward-only
(`CompiledGraph.trust_label`), which also makes it ineligible for a
holdout unseal (backend/api/routers/holdouts.py already refuses
`LIVE_ONLY` strategies).

Two adapters share this source id, chosen by the *caller*, never the node
(same "adapter is the only stateful thing, the node stays a pure dataclass"
shape as `FinvizScreenUniverseNode`):

- `DisabledAIJudgmentAdapter` — every context that isn't a live wallet day
  (Lab experiments, luck tests, neighbourhood scans, M9 unattended
  sessions, strategy validation, the CLI). Always passes, never calls an
  LLM, never costs money — DESIGN.md §5.2's "Lab runs... disable those
  nodes (and say so)" option.
- `LiveAIJudgmentAdapter` — the real daily wallet run only. Makes a real
  Anthropic call, records one `ai_judgments` row per judgment ("record,
  don't replay"), and enforces the wallet's `ai_daily_budget_usd`.

Budget-exhausted, a provider error, and an unparseable model response all
report as `AIJudgment(missing=True)` — from the node's point of view this
is exactly the same "couldn't get an answer" shape every other feature
lookup already has, so `CompiledGraph._apply_on_missing`'s existing
per-node `on_missing: fail_open | fail_closed` policy governs what happens
next with zero new engine mechanism.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.ai.provider import LLMProvider, ProviderError, TokenUsage
from backend.db.models import AiJudgment as AiJudgmentRow
from backend.db.models import Wallet
from backend.sources.registry import AlignmentPolicy, SourceSpec, TrustClass

AI_JUDGMENT_SOURCE_ID = "ai_judgment"

# Anthropic's published per-token pricing, $/million tokens. Approximate and
# may drift — shown to the user as an estimate, not a bill (CLAUDE.md:
# "never round a number in a flattering direction," so unknown models fall
# back to the priciest tier rather than the cheapest).
_PRICING_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-sonnet-4-5": (3.0, 15.0),
}
_DEFAULT_PRICING = (15.0, 75.0)


def _cost_usd(model: str, usage: TokenUsage) -> float:
    input_rate, output_rate = _PRICING_USD_PER_MTOK.get(model, _DEFAULT_PRICING)
    return (usage.input_tokens * input_rate + usage.output_tokens * output_rate) / 1_000_000


AI_JUDGMENT_SPEC = SourceSpec(
    id=AI_JUDGMENT_SOURCE_ID,
    features={},  # not an expression source — a veto-node adapter, same shape as finviz_screen
    trust_class=TrustClass.LIVE_ONLY,
    native_frequency="daily",
    alignment=AlignmentPolicy(native_frequency="daily"),
    coverage_note="Live judgments only — never backtestable (DESIGN.md §5.2). Lab/backtest "
                  "contexts register a disabled adapter that always passes without calling an LLM.",
)


@dataclass(frozen=True)
class AIJudgment:
    passed: bool
    reason: str
    explanation: str
    cost_usd: float = 0.0
    missing: bool = False


_JUDGE_TOOL_NAME = "record_judgment"

_JUDGE_TOOL = {
    "name": _JUDGE_TOOL_NAME,
    "description": "Record your judgment. You must call this exactly once.",
    "input_schema": {
        "type": "object",
        "properties": {
            "verdict": {
                "type": "string",
                "enum": ["ok", "concern"],
                "description": "'concern' blocks the trade; 'ok' lets it proceed.",
            },
            "explanation": {"type": "string", "description": "One or two plain-language sentences."},
        },
        "required": ["verdict", "explanation"],
    },
}

_PROMPTS: dict[str, str] = {
    "ai_news_check": (
        "You are a cautious swing-trading risk check. A strategy wants to buy {ticker} on {as_of}. "
        "Based on what you know, is there any notable red flag (scandal, litigation, guidance cut, "
        "regulatory action, etc.) that would make buying this stock around this date unwise? "
        "You do not have a live news feed — reason from general knowledge only, and say so if you "
        "are unsure. If in doubt, prefer 'ok' rather than inventing a concern."
    ),
    "ai_regime_check": (
        "You are a cautious swing-trading risk check. A strategy wants to open a new position on "
        "{as_of}. Based on what you know about the broad market conditions around this date, does "
        "the overall regime look too risky to open new equity positions right now (e.g. a crash, "
        "a credit crisis, extreme volatility)? If in doubt, prefer 'ok' rather than inventing a concern."
    ),
}


class DisabledAIJudgmentAdapter:
    """Registered everywhere except a real wallet's daily run. Never calls
    an LLM, never touches the database, never costs anything."""

    spec = AI_JUDGMENT_SPEC

    def judge(self, node_type: str, ticker: str, as_of: date) -> AIJudgment:
        return AIJudgment(
            passed=True,
            reason="ai_disabled_for_backtest",
            explanation=(
                "AI nodes are disabled during backtests and Lab experiments — this result "
                "reflects the strategy without its AI judgment (DESIGN.md §5.2)."
            ),
        )


class LiveAIJudgmentAdapter:
    """The only adapter that ever calls a real LLM or writes to
    `ai_judgments` — constructed fresh per wallet-day run
    (backend/worker/wallet_runner.py), never in a Lab/backtest context."""

    spec = AI_JUDGMENT_SPEC

    def __init__(self, session: Session, wallet: Wallet, provider: LLMProvider | None, model: str):
        self._session = session
        self._wallet = wallet
        self._provider = provider  # None when the wallet owner has no API key configured
        self._model = model

    def judge(self, node_type: str, ticker: str, as_of: date) -> AIJudgment:
        if self._provider is None:
            return AIJudgment(
                passed=False, reason="ai_no_api_key",
                explanation="No Anthropic key is configured for this wallet's owner — add one in Settings.",
                missing=True,
            )

        budget = self._wallet.ai_daily_budget_usd
        if budget is not None:
            spent_today = self._session.execute(
                select(AiJudgmentRow.cost_usd).where(
                    AiJudgmentRow.wallet_id == self._wallet.id,
                    AiJudgmentRow.as_of == as_of,
                )
            ).scalars().all()
            if sum(float(c) for c in spent_today) >= float(budget):
                return AIJudgment(
                    passed=False,
                    reason="ai_budget_exhausted",
                    explanation="Today's AI budget for this wallet is used up.",
                    missing=True,
                )

        prompt = _PROMPTS[node_type].format(ticker=ticker, as_of=as_of.isoformat())
        input_context = {"node_type": node_type, "ticker": ticker, "as_of": as_of.isoformat()}
        try:
            response = self._provider.complete(
                messages=[{"role": "user", "content": prompt}],
                tools=[_JUDGE_TOOL],
                system=(
                    "You are a risk-check node inside a swing-trading strategy. Always call "
                    f"{_JUDGE_TOOL_NAME} exactly once with your verdict — never answer in plain text."
                ),
            )
        except ProviderError as exc:
            return AIJudgment(
                passed=False, reason="ai_provider_error", explanation=str(exc), missing=True,
            )

        cost = _cost_usd(self._model, response.usage)
        call = next((c for c in response.tool_calls if c.name == _JUDGE_TOOL_NAME), None)
        if call is None:
            self._record(as_of, node_type, ticker, input_context, {"error": "no_tool_call"}, cost)
            return AIJudgment(
                passed=False, reason="ai_response_unparseable",
                explanation="The AI did not return a usable judgment.", cost_usd=cost, missing=True,
            )

        verdict = call.input.get("verdict")
        explanation = call.input.get("explanation", "")
        self._record(as_of, node_type, ticker, input_context, call.input, cost)
        return AIJudgment(
            passed=(verdict == "ok"),
            reason=f"ai_verdict_{verdict}",
            explanation=explanation,
            cost_usd=cost,
        )

    def _record(
        self, as_of: date, node_type: str, ticker: str, input_context: dict, output: dict, cost: float
    ) -> None:
        self._session.add(
            AiJudgmentRow(
                wallet_id=self._wallet.id, as_of=as_of, node_type=node_type, ticker=ticker,
                input_context_json=input_context, output_json=output, model=self._model,
                cost_usd=cost,
            )
        )
        self._session.flush()
