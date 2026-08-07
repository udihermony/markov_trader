"""The copilot's turn loop (DESIGN.md §5.1: "the primary AI surface... its
output is a deterministic strategy graph: nodes, parameters, and feature
expressions — fully backtestable, fully inspectable"). Synchronous for v1
(confirmed with the user) — a turn runs to completion inside one request,
matching every other mutation in this app.
"""
from __future__ import annotations

import json

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.ai.provider import LLMProvider, get_provider_for_user
from backend.ai.tools import TOOL_DEFINITIONS, execute_tool
from backend.db.models import ChatMessage, Conversation, User

MAX_TOOL_ITERATIONS = 6

SYSTEM_PROMPT = """You are the copilot inside Markov Trader, a swing-trading practice app \
for people who are not professional traders.

Ground rules, non-negotiable:
- The user is not a trader. Never use jargon without a plain-language equivalent in the \
same sentence.
- Be willing to say no. A strategy idea that is obviously overfit, untested, or a bad idea \
does not get dressed up as promising — say so plainly.
- create_strategy and update_strategy never save anything by themselves. They only produce \
a proposal for the human to review; call validate_strategy first to check it's structurally \
sound before proposing it.
- run_backtest requires a real hypothesis and expected outcome stated before you run it, not \
written to match the result afterward.
- You cannot create, modify, or delete a wallet, approve an order, or spend a holdout unseal \
— you have no tools for any of that, and must never claim to have done so.
- Every Lab result is contaminated by search unless it came from a holdout — say so when it's \
relevant.
"""


def _resolve_context_blurb(db: Session, user: User, context: dict | None) -> str:
    if not context or not context.get("surface"):
        return ""
    surface = context["surface"]
    entity_id = context.get("entity_id")
    if surface == "strategy" and entity_id:
        result = execute_tool("get_strategy", {"strategy_id": entity_id}, db, user)
        if "error" not in result:
            return f'\n\nThe user is currently looking at strategy #{entity_id}, "{result["name"]}".'
    if surface == "wallet" and entity_id:
        result = execute_tool("get_wallet", {"wallet_id": entity_id}, db, user)
        if "error" not in result:
            return f'\n\nThe user is currently looking at wallet #{entity_id}, "{result["name"]}".'
    return f"\n\nThe user is currently on the {surface} page."


def create_conversation(db: Session, user: User) -> Conversation:
    conversation = Conversation(user_id=user.id)
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


def _owned_conversation(db: Session, user: User, conversation_id: int) -> Conversation:
    conversation = db.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="conversation not found")
    return conversation


def run_turn(
    db: Session,
    user: User,
    conversation_id: int,
    message: str,
    context: dict | None = None,
    provider: LLMProvider | None = None,
) -> ChatMessage:
    _owned_conversation(db, user, conversation_id)

    db.add(ChatMessage(conversation_id=conversation_id, role="user", content=message))
    db.commit()

    if provider is None:
        provider = get_provider_for_user(db, user)

    history = db.execute(
        select(ChatMessage).where(ChatMessage.conversation_id == conversation_id).order_by(ChatMessage.id)
    ).scalars().all()
    messages: list[dict] = [{"role": r.role, "content": r.content} for r in history]

    system = SYSTEM_PROMPT + _resolve_context_blurb(db, user, context)

    proposal: dict | None = None
    final_text: str | None = None
    for _ in range(MAX_TOOL_ITERATIONS):
        response = provider.complete(messages, TOOL_DEFINITIONS, system)
        if response.text:
            final_text = response.text
        if response.stop_reason != "tool_use" or not response.tool_calls or proposal is not None:
            break
        messages.append({"role": "assistant", "content": response.raw_content})
        tool_result_blocks = []
        for call in response.tool_calls:
            result = execute_tool(call.name, call.input, db, user)
            if isinstance(result, dict) and result.get("proposal"):
                proposal = result
            tool_result_blocks.append(
                {"type": "tool_result", "tool_use_id": call.id, "content": json.dumps(result)}
            )
        messages.append({"role": "user", "content": tool_result_blocks})
    else:
        final_text = final_text or (
            "I wasn't able to finish that within the allowed number of steps — "
            "try breaking the request into smaller pieces."
        )

    if final_text is None:
        final_text = "Here's what I'm proposing — review it below." if proposal else "I don't have a response for that."

    assistant_row = ChatMessage(
        conversation_id=conversation_id, role="assistant", content=final_text, proposal_json=proposal
    )
    db.add(assistant_row)
    db.commit()
    db.refresh(assistant_row)
    return assistant_row
