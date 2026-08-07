from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import backend.ai.copilot as copilot_module
from backend.ai.provider import ProviderError, ProviderResponse
from backend.api.deps import get_db
from backend.api.main import app


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


def _auth_headers(client, email="chat-user@example.com") -> dict:
    client.post("/auth/register", json={"email": email, "password": "hunter2pw"})
    res = client.post("/auth/login", json={"email": email, "password": "hunter2pw"})
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


class _FakeProvider:
    def complete(self, messages, tools, system):
        return ProviderResponse(
            text="Hello! What would you like to build?", tool_calls=[], stop_reason="end_turn",
            raw_content=[{"type": "text", "text": "Hello! What would you like to build?"}],
        )


def test_posting_a_message_without_an_api_key_returns_422(client):
    headers = _auth_headers(client)
    conversation = client.post("/chat/conversations", headers=headers).json()

    res = client.post(
        f"/chat/conversations/{conversation['id']}/messages", json={"content": "hi"}, headers=headers
    )
    assert res.status_code == 422
    assert "Settings" in res.json()["detail"]


class _FailingProvider:
    def complete(self, messages, tools, system):
        raise ProviderError("Anthropic API error: credit balance too low")


def test_provider_failure_returns_clean_502_not_a_crash(client, monkeypatch):
    monkeypatch.setattr(copilot_module, "get_provider_for_user", lambda db, user: _FailingProvider())
    headers = _auth_headers(client)
    conversation = client.post("/chat/conversations", headers=headers).json()

    res = client.post(
        f"/chat/conversations/{conversation['id']}/messages", json={"content": "hi"}, headers=headers
    )
    assert res.status_code == 502
    assert "credit balance" in res.json()["detail"]


def test_full_round_trip_with_monkeypatched_provider(client, monkeypatch):
    monkeypatch.setattr(copilot_module, "get_provider_for_user", lambda db, user: _FakeProvider())
    headers = _auth_headers(client)
    conversation = client.post("/chat/conversations", headers=headers).json()

    res = client.post(
        f"/chat/conversations/{conversation['id']}/messages", json={"content": "hi there"}, headers=headers
    )
    assert res.status_code == 200
    assert res.json()["role"] == "assistant"
    assert res.json()["content"] == "Hello! What would you like to build?"

    history = client.get(f"/chat/conversations/{conversation['id']}/messages", headers=headers).json()
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert history[0]["content"] == "hi there"


def test_conversations_scoped_to_owner(client):
    headers_a = _auth_headers(client, "a@example.com")
    headers_b = _auth_headers(client, "b@example.com")
    client.post("/chat/conversations", headers=headers_a)

    assert len(client.get("/chat/conversations", headers=headers_a).json()) == 1
    assert len(client.get("/chat/conversations", headers=headers_b).json()) == 0


def test_messages_for_unowned_conversation_returns_404(client):
    headers_a = _auth_headers(client, "c@example.com")
    headers_b = _auth_headers(client, "d@example.com")
    conversation = client.post("/chat/conversations", headers=headers_a).json()

    res = client.get(f"/chat/conversations/{conversation['id']}/messages", headers=headers_b)
    assert res.status_code == 404

    res = client.post(f"/chat/conversations/{conversation['id']}/messages", json={"content": "hi"}, headers=headers_b)
    assert res.status_code == 404
