from __future__ import annotations

from datetime import date

from sqlalchemy import select

from backend.ai.provider import ProviderError, ProviderResponse, ToolCall, TokenUsage
from backend.db.models import AiJudgment as AiJudgmentRow
from backend.sources.ai_judgment import DisabledAIJudgmentAdapter, LiveAIJudgmentAdapter


class FakeProvider:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def complete(self, messages, tools, system):
        self.calls.append((messages, tools, system))
        return self.script.pop(0)


def _verdict_response(verdict: str, explanation: str = "looks fine", *, input_tokens=100, output_tokens=20):
    call = ToolCall(id="call_1", name="record_judgment", input={"verdict": verdict, "explanation": explanation})
    return ProviderResponse(
        text=None, tool_calls=[call], stop_reason="tool_use", raw_content=[],
        usage=TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens),
    )


def _text_only_response():
    return ProviderResponse(
        text="hmm", tool_calls=[], stop_reason="end_turn", raw_content=[], usage=TokenUsage(10, 5),
    )


def test_disabled_adapter_always_passes_with_no_cost():
    adapter = DisabledAIJudgmentAdapter()
    result = adapter.judge("ai_regime_check", "AAPL", date(2026, 3, 1))
    assert result.passed is True
    assert result.cost_usd == 0.0
    assert result.missing is False


def test_live_adapter_records_row_and_returns_verdict(db_session, wallet):
    provider = FakeProvider([_verdict_response("ok", "no concerns")])
    adapter = LiveAIJudgmentAdapter(db_session, wallet, provider, model="claude-sonnet-4-5")

    result = adapter.judge("ai_news_check", "AAPL", date(2026, 3, 1))

    assert result.passed is True
    assert result.missing is False
    assert result.cost_usd > 0

    rows = db_session.execute(
        select(AiJudgmentRow).where(AiJudgmentRow.wallet_id == wallet.id)
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].node_type == "ai_news_check"
    assert rows[0].ticker == "AAPL"
    assert rows[0].as_of == date(2026, 3, 1)
    assert float(rows[0].cost_usd) == result.cost_usd


def test_live_adapter_concern_verdict_blocks(db_session, wallet):
    provider = FakeProvider([_verdict_response("concern", "guidance cut")])
    adapter = LiveAIJudgmentAdapter(db_session, wallet, provider, model="claude-sonnet-4-5")

    result = adapter.judge("ai_news_check", "AAPL", date(2026, 3, 1))

    assert result.passed is False
    assert result.explanation == "guidance cut"


def test_live_adapter_reports_missing_on_budget_exhausted(db_session, wallet):
    wallet.ai_daily_budget_usd = 0.0001
    db_session.flush()
    provider = FakeProvider([_verdict_response("ok")])
    adapter = LiveAIJudgmentAdapter(db_session, wallet, provider, model="claude-sonnet-4-5")

    first = adapter.judge("ai_news_check", "AAPL", date(2026, 3, 1))
    assert first.missing is False
    assert first.cost_usd > 0

    second = adapter.judge("ai_news_check", "MSFT", date(2026, 3, 1))
    assert second.missing is True
    assert second.reason == "ai_budget_exhausted"
    # no second LLM call was made once the budget was already spent
    assert len(provider.calls) == 1


def test_live_adapter_budget_resets_per_day(db_session, wallet):
    wallet.ai_daily_budget_usd = 0.0001
    db_session.flush()
    provider = FakeProvider([_verdict_response("ok"), _verdict_response("ok")])
    adapter = LiveAIJudgmentAdapter(db_session, wallet, provider, model="claude-sonnet-4-5")

    adapter.judge("ai_news_check", "AAPL", date(2026, 3, 1))
    next_day = adapter.judge("ai_news_check", "AAPL", date(2026, 3, 2))
    assert next_day.missing is False


def test_live_adapter_reports_missing_when_no_provider(db_session, wallet):
    adapter = LiveAIJudgmentAdapter(db_session, wallet, None, model="claude-sonnet-4-5")
    result = adapter.judge("ai_news_check", "AAPL", date(2026, 3, 1))
    assert result.missing is True
    assert result.reason == "ai_no_api_key"


def test_live_adapter_reports_missing_on_provider_error(db_session, wallet):
    class ErrorProvider:
        def complete(self, messages, tools, system):
            raise ProviderError("boom")

    adapter = LiveAIJudgmentAdapter(db_session, wallet, ErrorProvider(), model="claude-sonnet-4-5")
    result = adapter.judge("ai_news_check", "AAPL", date(2026, 3, 1))
    assert result.missing is True
    assert result.reason == "ai_provider_error"

    rows = db_session.execute(
        select(AiJudgmentRow).where(AiJudgmentRow.wallet_id == wallet.id)
    ).scalars().all()
    assert len(rows) == 0  # no completed call, nothing to record


def test_live_adapter_reports_missing_on_unparseable_response(db_session, wallet):
    provider = FakeProvider([_text_only_response()])
    adapter = LiveAIJudgmentAdapter(db_session, wallet, provider, model="claude-sonnet-4-5")

    result = adapter.judge("ai_news_check", "AAPL", date(2026, 3, 1))
    assert result.missing is True
    assert result.reason == "ai_response_unparseable"

    rows = db_session.execute(
        select(AiJudgmentRow).where(AiJudgmentRow.wallet_id == wallet.id)
    ).scalars().all()
    assert len(rows) == 1  # a real call happened and cost tokens — still recorded
