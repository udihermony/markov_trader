from __future__ import annotations

from backend.ai.provider import _clean_error_message


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
