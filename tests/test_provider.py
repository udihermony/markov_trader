from __future__ import annotations

from backend.ai.provider import AnthropicProvider, TokenUsage, _clean_error_message


class _FakeSdkError(Exception):
    def __init__(self, message: str, body: object | None):
        super().__init__(message)
        self.body = body


def test_clean_error_message_prefers_the_api_body_message():
    exc = _FakeSdkError(
        "Error code: 400 - {'type': 'error', 'error': {...}}",
        body={"type": "error", "error": {"type": "invalid_request_error", "message": "Your credit balance is too low."}},
    )
    assert _clean_error_message(exc) == "Your credit balance is too low."


def test_clean_error_message_falls_back_to_str_without_a_body():
    exc = _FakeSdkError("Connection error.", body=None)
    assert _clean_error_message(exc) == "Connection error."


def test_token_usage_adds():
    total = TokenUsage(input_tokens=10, output_tokens=5) + TokenUsage(input_tokens=3, output_tokens=7)
    assert total == TokenUsage(input_tokens=13, output_tokens=12)


class _FakeBlock:
    def __init__(self, data: dict):
        self._data = data

    def model_dump(self) -> dict:
        return self._data


class _FakeUsage:
    def __init__(self, input_tokens: int, output_tokens: int):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeAnthropicResponse:
    def __init__(self, content: list[dict], stop_reason: str, usage: _FakeUsage):
        self.content = [_FakeBlock(b) for b in content]
        self.stop_reason = stop_reason
        self.usage = usage


def test_anthropic_provider_captures_token_usage(monkeypatch):
    provider = AnthropicProvider(api_key="sk-ant-fake-for-test")
    fake_response = _FakeAnthropicResponse(
        content=[{"type": "text", "text": "hi"}], stop_reason="end_turn", usage=_FakeUsage(42, 17),
    )
    monkeypatch.setattr(provider._client.messages, "create", lambda **kwargs: fake_response)  # noqa: SLF001

    result = provider.complete(messages=[{"role": "user", "content": "hello"}], tools=[], system="")

    assert result.usage == TokenUsage(input_tokens=42, output_tokens=17)
