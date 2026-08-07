from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.ai.copilot import create_conversation, run_turn
from backend.ai.provider import NoApiKeyError, ProviderError
from backend.api.deps import get_current_user, get_db
from backend.db.models import ChatMessage, Conversation, User

router = APIRouter(prefix="/chat", tags=["chat"])


class ConversationResponse(BaseModel):
    id: int
    created_at: datetime


class ChatMessageResponse(BaseModel):
    id: int
    role: str
    content: str
    proposal_json: dict | None
    created_at: datetime


class PostMessageRequest(BaseModel):
    content: str = Field(min_length=1)
    context: dict | None = None


def _to_message_response(m: ChatMessage) -> ChatMessageResponse:
    return ChatMessageResponse(
        id=m.id, role=m.role, content=m.content, proposal_json=m.proposal_json, created_at=m.created_at
    )


@router.post("/conversations", response_model=ConversationResponse, status_code=status.HTTP_201_CREATED)
def create_conversation_route(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> ConversationResponse:
    conversation = create_conversation(db, user)
    return ConversationResponse(id=conversation.id, created_at=conversation.created_at)


@router.get("/conversations", response_model=list[ConversationResponse])
def list_conversations(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[ConversationResponse]:
    rows = db.execute(
        select(Conversation).where(Conversation.user_id == user.id).order_by(Conversation.created_at.desc())
    ).scalars().all()
    return [ConversationResponse(id=r.id, created_at=r.created_at) for r in rows]


def _owned_conversation_or_404(conversation_id: int, user: User, db: Session) -> Conversation:
    conversation = db.execute(
        select(Conversation).where(Conversation.id == conversation_id, Conversation.user_id == user.id)
    ).scalar_one_or_none()
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="conversation not found")
    return conversation


@router.get("/conversations/{conversation_id}/messages", response_model=list[ChatMessageResponse])
def list_messages(
    conversation_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[ChatMessageResponse]:
    _owned_conversation_or_404(conversation_id, user, db)
    rows = db.execute(
        select(ChatMessage).where(ChatMessage.conversation_id == conversation_id).order_by(ChatMessage.id)
    ).scalars().all()
    return [_to_message_response(m) for m in rows]


@router.post("/conversations/{conversation_id}/messages", response_model=ChatMessageResponse)
def post_message(
    conversation_id: int,
    payload: PostMessageRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ChatMessageResponse:
    _owned_conversation_or_404(conversation_id, user, db)
    try:
        assistant_message = run_turn(db, user, conversation_id, payload.content, context=payload.context)
    except NoApiKeyError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="no Anthropic API key set — add one in Settings",
        ) from None
    except ProviderError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from None
    return _to_message_response(assistant_message)
