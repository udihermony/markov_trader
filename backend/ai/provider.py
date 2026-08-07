"""LLM provider abstraction (DESIGN.md §5.6): "A `LLMProvider` interface
with `complete(messages, tools) -> Response`. Anthropic is the first and
only implementation in v1." Every copilot code path (backend/ai/copilot.py)
talks to this Protocol, never to the Anthropic SDK directly — swapping in
OpenAI or a local model later is an new implementation of this interface,
not a rewrite of the tool loop.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.api.encryption import decrypt
from backend.db.models import ApiKey, User

DEFAULT_MODEL = "claude-sonnet-4-5"
MAX_TOKENS = 2048


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass(frozen=True)
class TokenUsage:
    """DESIGN.md §5.4: "per-user token budget with visible spend." Visible
    only for v1 (M9 plan) — every call's real usage is captured here and
    summed by callers (backend/ai/unattended.py), nothing is hard-capped yet."""

    input_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(self.input_tokens + other.input_tokens, self.output_tokens + other.output_tokens)


@dataclass(frozen=True)
class ProviderResponse:
    text: str | None
    tool_calls: list[ToolCall]
    stop_reason: str  # "end_turn" | "tool_use" | ...
    # The raw content blocks from the provider, needed verbatim to continue
    # a tool-use conversation (the assistant turn that requested tools must
    # be echoed back exactly in the next request's message history).
    raw_content: list[dict] = field(default_factory=list)
    usage: TokenUsage = field(default_factory=TokenUsage)


class LLMProvider(Protocol):
    def complete(self, messages: list[dict], tools: list[dict], system: str) -> ProviderResponse: ...


class NoApiKeyError(Exception):
    """Raised when a user has no stored key for the requested provider —
    the chat endpoint turns this into a 422 pointing at Settings."""


class ProviderError(Exception):
    """Wraps any failure from the underlying LLM SDK (bad key, no credit,
    rate limit, network) into one clean, chat-endpoint-catchable type —
    the chat endpoint turns this into a 502 with a readable detail instead
    of an unhandled 500 leaking a raw SDK traceback to the user."""


def _clean_error_message(exc: Exception) -> str:
    """Anthropic's default exception message is `f"Error code: {status} -
    {full_body_dict}"` — readable enough for logs, not for a chat bubble.
    Prefer the API's own human-readable `error.message` field when the SDK
    gives us a parsed body to pull it from."""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        message = body.get("error", {}).get("message") if isinstance(body.get("error"), dict) else None
        if message:
            return message
    return str(exc)


class AnthropicProvider:
    def __init__(self, api_key: str, model: str = DEFAULT_MODEL):
        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def complete(self, messages: list[dict], tools: list[dict], system: str) -> ProviderResponse:
        import anthropic

        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=MAX_TOKENS,
                system=system,
                messages=messages,
                tools=tools,
            )
        except anthropic.APIError as exc:
            raise ProviderError(f"Anthropic API error: {_clean_error_message(exc)}") from exc
        raw_content = [block.model_dump() for block in response.content]
        text_parts = [b["text"] for b in raw_content if b["type"] == "text"]
        tool_calls = [
            ToolCall(id=b["id"], name=b["name"], input=b["input"])
            for b in raw_content if b["type"] == "tool_use"
        ]
        return ProviderResponse(
            text="\n".join(text_parts) if text_parts else None,
            tool_calls=tool_calls,
            stop_reason=response.stop_reason,
            raw_content=raw_content,
            usage=TokenUsage(
                input_tokens=response.usage.input_tokens, output_tokens=response.usage.output_tokens
            ),
        )


def get_provider_for_user(db: Session, user: User, *, provider: str = "anthropic") -> LLMProvider:
    row = db.execute(
        select(ApiKey).where(ApiKey.user_id == user.id, ApiKey.provider == provider)
    ).scalar_one_or_none()
    if row is None:
        raise NoApiKeyError(f"no {provider} API key set for this user")
    key = decrypt(row.encrypted_key)
    model = os.environ.get("COPILOT_MODEL", DEFAULT_MODEL)
    return AnthropicProvider(api_key=key, model=model)
