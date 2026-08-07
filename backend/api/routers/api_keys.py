from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.api.deps import get_current_user, get_db
from backend.api.encryption import encrypt
from backend.db.models import ApiKey, User

router = APIRouter(prefix="/api-keys", tags=["api-keys"])

SUPPORTED_PROVIDERS = {"anthropic"}


class SaveApiKeyRequest(BaseModel):
    provider: str
    key: str = Field(min_length=1)


class ApiKeyResponse(BaseModel):
    provider: str
    created_at: datetime


@router.post("", response_model=ApiKeyResponse, status_code=status.HTTP_201_CREATED)
def save_api_key(
    payload: SaveApiKeyRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiKeyResponse:
    if payload.provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"unsupported provider {payload.provider!r}")

    existing = db.execute(
        select(ApiKey).where(ApiKey.user_id == user.id, ApiKey.provider == payload.provider)
    ).scalar_one_or_none()
    encrypted = encrypt(payload.key)
    if existing is not None:
        existing.encrypted_key = encrypted
        row = existing
    else:
        row = ApiKey(user_id=user.id, provider=payload.provider, encrypted_key=encrypted)
        db.add(row)
    db.commit()
    db.refresh(row)
    return ApiKeyResponse(provider=row.provider, created_at=row.created_at)


@router.get("", response_model=list[ApiKeyResponse])
def list_api_keys(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[ApiKeyResponse]:
    rows = db.execute(select(ApiKey).where(ApiKey.user_id == user.id)).scalars().all()
    return [ApiKeyResponse(provider=r.provider, created_at=r.created_at) for r in rows]


@router.delete("/{provider}", status_code=status.HTTP_204_NO_CONTENT)
def delete_api_key(provider: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> None:
    row = db.execute(
        select(ApiKey).where(ApiKey.user_id == user.id, ApiKey.provider == provider)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no key set for this provider")
    db.delete(row)
    db.commit()
