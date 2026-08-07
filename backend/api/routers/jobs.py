from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.api.deps import get_current_user, get_db
from backend.api.routers.strategies import _owned_or_404
from backend.db.models import Job, User
from backend.worker.jobs import enqueue_job

router = APIRouter(prefix="/jobs", tags=["jobs"])


class CreateUnattendedSessionRequest(BaseModel):
    strategy_id: int
    goal: str = Field(min_length=1)
    budget: int = Field(default=10, ge=1, le=50)


class JobResponse(BaseModel):
    id: int
    type: str
    payload_json: dict
    status: str
    progress: dict | None
    result_json: dict | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


def _to_response(job: Job) -> JobResponse:
    return JobResponse(
        id=job.id, type=job.type, payload_json=job.payload_json, status=job.status,
        progress=job.progress, result_json=job.result_json, created_at=job.created_at,
        started_at=job.started_at, finished_at=job.finished_at,
    )


@router.post("/unattended-sessions", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
def create_unattended_session(
    payload: CreateUnattendedSessionRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> JobResponse:
    _owned_or_404(payload.strategy_id, user, db)  # 404s if not owned
    job = enqueue_job(
        db, user, "unattended_experiment_session",
        {"strategy_id": payload.strategy_id, "goal": payload.goal, "budget": payload.budget},
    )
    return _to_response(job)


@router.get("", response_model=list[JobResponse])
def list_jobs(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[JobResponse]:
    rows = db.execute(
        select(Job).where(Job.user_id == user.id).order_by(Job.created_at.desc())
    ).scalars().all()
    return [_to_response(j) for j in rows]


@router.get("/{job_id}", response_model=JobResponse)
def get_job(job_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> JobResponse:
    job = db.execute(
        select(Job).where(Job.id == job_id, Job.user_id == user.id)
    ).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
    return _to_response(job)
