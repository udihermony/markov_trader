from __future__ import annotations

from sqlalchemy import select

import backend.engine.graph.nodes  # noqa: F401  registers the node type library
from backend.ai.copilot import create_conversation, run_turn
from backend.ai.provider import ProviderResponse, ToolCall
from backend.db.models import ChatMessage, Strategy, User

VALID_SPEC = {
    "spec_version": 2,
    "name": "sma-crossover",
    "sources": [{"id": "px", "type": "price_bars"}],
    "nodes": [
        {"id": "u1", "kind": "universe", "type": "manual_list", "params": {"tickers": ["AAPL"]}},
        {"id": "t1", "kind": "trigger", "type": "cross",
         "params": {"a": "sma(px.close, 10)", "b": "sma(px.close, 20)", "direction": "up"}},
        {"id": "x1", "kind": "exit", "type": "cross",
         "params": {"a": "sma(px.close, 10)", "b": "sma(px.close, 20)", "direction": "down"}},
        {"id": "s1", "kind": "size", "type": "fixed_fraction", "params": {"fraction": 0.1}},
    ],
    "edges": [["u1", "t1"]],
}


class FakeProvider:
    """Scripted LLMProvider test double — pops one canned ProviderResponse
    per `complete()` call. No test in this file ever reaches the real
    Anthropic SDK or network."""

    def __init__(self, script: list[ProviderResponse]):
        self.script = list(script)
        self.calls: list[list[dict]] = []

    def complete(self, messages, tools, system) -> ProviderResponse:
        self.calls.append(messages)
        return self.script.pop(0)


def _make_user(db_session) -> User:
    user = User(email="copilot@example.com", password_hash="x")
    db_session.add(user)
    db_session.flush()
    return user


def _text_response(text: str) -> ProviderResponse:
    return ProviderResponse(
        text=text, tool_calls=[], stop_reason="end_turn",
        raw_content=[{"type": "text", "text": text}],
    )


def _tool_call_response(name: str, tool_input: dict, call_id: str = "call_1", text: str | None = None) -> ProviderResponse:
    content = ([{"type": "text", "text": text}] if text else []) + [
        {"type": "tool_use", "id": call_id, "name": name, "input": tool_input}
    ]
    return ProviderResponse(
        text=text, tool_calls=[ToolCall(id=call_id, name=name, input=tool_input)],
        stop_reason="tool_use", raw_content=content,
    )


def test_plain_text_turn_persists_both_messages(db_session):
    user = _make_user(db_session)
    conversation = create_conversation(db_session, user)
    provider = FakeProvider([_text_response("Hello! What would you like to build?")])

    result = run_turn(db_session, user, conversation.id, "hi there", provider=provider)

    assert result.role == "assistant"
    assert result.content == "Hello! What would you like to build?"
    assert result.proposal_json is None

    rows = db_session.execute(
        select(ChatMessage).where(ChatMessage.conversation_id == conversation.id).order_by(ChatMessage.id)
    ).scalars().all()
    assert [r.role for r in rows] == ["user", "assistant"]
    assert rows[0].content == "hi there"


def test_tool_call_is_executed_and_fed_back(db_session):
    user = _make_user(db_session)
    db_session.add(Strategy(user_id=user.id, name="Existing", spec_json=VALID_SPEC, spec_version=2))
    db_session.flush()
    conversation = create_conversation(db_session, user)

    provider = FakeProvider([
        _tool_call_response("list_strategies", {}),
        _text_response("You have one strategy: Existing."),
    ])

    result = run_turn(db_session, user, conversation.id, "what strategies do I have?", provider=provider)

    assert result.content == "You have one strategy: Existing."
    assert result.proposal_json is None
    # the second call's message history includes the tool_result from the first
    second_call_messages = provider.calls[1]
    assert any(m["role"] == "user" and isinstance(m["content"], list) for m in second_call_messages)


def test_create_strategy_tool_surfaces_as_proposal_and_stops(db_session):
    user = _make_user(db_session)
    conversation = create_conversation(db_session, user)

    provider = FakeProvider([
        _tool_call_response("create_strategy", {"name": "Proposed", "spec": VALID_SPEC}, text="Let me draft that."),
        _text_response("Here's a moving-average crossover strategy for AAPL."),
    ])

    result = run_turn(db_session, user, conversation.id, "build me a strategy", provider=provider)

    assert result.proposal_json is not None
    assert result.proposal_json["proposal"] is True
    assert result.proposal_json["kind"] == "create"
    assert result.content == "Here's a moving-average crossover strategy for AAPL."

    # nothing was actually saved
    count = db_session.execute(select(Strategy)).scalars().all()
    assert len(count) == 0


def test_loop_is_bounded_when_provider_always_calls_tools(db_session):
    user = _make_user(db_session)
    db_session.add(Strategy(user_id=user.id, name="Existing", spec_json=VALID_SPEC, spec_version=2))
    db_session.flush()
    conversation = create_conversation(db_session, user)

    provider = FakeProvider([_tool_call_response("list_strategies", {}, call_id=f"call_{i}") for i in range(10)])

    result = run_turn(db_session, user, conversation.id, "keep going forever", provider=provider)

    assert len(provider.calls) == 6  # MAX_TOOL_ITERATIONS, not 10
    assert "wasn't able to finish" in result.content


def test_conversation_scoped_to_owner(db_session):
    import pytest
    from fastapi import HTTPException

    user_a = _make_user(db_session)
    user_b = User(email="copilot-b@example.com", password_hash="x")
    db_session.add(user_b)
    db_session.flush()
    conversation = create_conversation(db_session, user_a)

    with pytest.raises(HTTPException):
        run_turn(db_session, user_b, conversation.id, "hi", provider=FakeProvider([_text_response("hi")]))
