from __future__ import annotations

from dataclasses import replace
from datetime import date

from backend.engine.graph.compiled import _apply_on_missing
from backend.engine.graph.nodes import AINewsCheckNode, AIRegimeCheckNode
from backend.engine.graph.spec import NodeSpec
from backend.engine.graph.types import NodeContext, PortfolioView
from backend.sources.ai_judgment import AIJudgment, DisabledAIJudgmentAdapter


def make_ctx(ticker: str = "AAPL", as_of: date = date(2026, 3, 1)) -> NodeContext:
    # AI nodes never touch ctx.features — a bare stand-in is fine here.
    return NodeContext(
        features=None, as_of=as_of, ticker=ticker, position=None,
        portfolio=PortfolioView(cash=100_000.0, open_position_count=0),
    )


class ScriptedAdapter:
    def __init__(self, result: AIJudgment):
        self.result = result
        self.calls: list[tuple[str, str, date]] = []

    def judge(self, node_type, ticker, as_of):
        self.calls.append((node_type, ticker, as_of))
        return self.result


def test_disabled_adapter_via_news_check_node_always_passes():
    node = AINewsCheckNode(adapter=DisabledAIJudgmentAdapter())
    result = node.evaluate(make_ctx())
    assert result.passed is True
    assert result.missing == []


def test_regime_check_node_calls_adapter_with_regime_node_type():
    adapter = ScriptedAdapter(AIJudgment(passed=True, reason="ai_verdict_ok", explanation="fine"))
    node = AIRegimeCheckNode(adapter=adapter)
    node.evaluate(make_ctx(ticker="MSFT", as_of=date(2026, 5, 1)))
    assert adapter.calls == [("ai_regime_check", "MSFT", date(2026, 5, 1))]


def test_news_check_node_blocks_on_concern_verdict():
    adapter = ScriptedAdapter(AIJudgment(passed=False, reason="ai_verdict_concern", explanation="bad news"))
    node = AINewsCheckNode(adapter=adapter)
    result = node.evaluate(make_ctx())
    assert result.passed is False
    assert result.explanation == "bad news"
    assert result.missing == []


def test_missing_judgment_marks_result_missing_for_on_missing_policy():
    adapter = ScriptedAdapter(
        AIJudgment(passed=False, reason="ai_budget_exhausted", explanation="used up", missing=True)
    )
    node = AINewsCheckNode(adapter=adapter)
    result = node.evaluate(make_ctx())
    assert result.missing == ["ai_judgment"]


def test_on_missing_fail_open_lets_trade_through_despite_exhausted_budget():
    adapter = ScriptedAdapter(
        AIJudgment(passed=False, reason="ai_budget_exhausted", explanation="used up", missing=True)
    )
    node = AINewsCheckNode(adapter=adapter)
    raw = node.evaluate(make_ctx())
    spec = NodeSpec(id="ai1", kind="veto", type="ai_news_check", params={}, on_missing="fail_open")
    assert _apply_on_missing(raw, spec).passed is True


def test_on_missing_fail_closed_keeps_blocking_on_exhausted_budget():
    adapter = ScriptedAdapter(
        AIJudgment(passed=False, reason="ai_budget_exhausted", explanation="used up", missing=True)
    )
    node = AINewsCheckNode(adapter=adapter)
    raw = node.evaluate(make_ctx())
    spec = NodeSpec(id="ai1", kind="veto", type="ai_news_check", params={}, on_missing="fail_closed")
    assert _apply_on_missing(raw, spec).passed is False
