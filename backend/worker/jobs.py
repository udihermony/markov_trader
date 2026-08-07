"""The Postgres job queue DESIGN.md §7 describes ("`SELECT ... FOR UPDATE
SKIP LOCKED`... the POC's single `threading.Lock` does not survive"),
deferred since M4 (`wallet_runner.py`'s docstring: "No jobs table... deferred
to M9, where a UI first needs to read job status/progress") because nothing
needed it until unattended AI sessions (backend/ai/unattended.py) did.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable, ContextManager

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.ai.unattended import run_unattended_session
from backend.db.models import Job, User
from backend.db.session import get_session

log = logging.getLogger(__name__)


def enqueue_job(db: Session, user: User, job_type: str, payload: dict) -> Job:
    job = Job(user_id=user.id, type=job_type, payload_json=payload, status="pending")
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def claim_next_job(session: Session) -> Job | None:
    """`FOR UPDATE SKIP LOCKED` — a second poller hitting this at the same
    moment skips any row already locked by the first, rather than blocking
    on it or double-claiming it (the actual DESIGN.md §7 mechanism, not just
    a single-process assumption)."""
    job = session.execute(
        select(Job)
        .where(Job.status == "pending")
        .order_by(Job.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).scalar_one_or_none()
    if job is None:
        return None
    job.status = "running"
    job.started_at = datetime.now(timezone.utc)
    session.commit()
    session.refresh(job)
    return job


def run_job(session: Session, job: Job) -> None:
    try:
        if job.type == "unattended_experiment_session":
            user = session.get(User, job.user_id)
            payload = job.payload_json
            result = run_unattended_session(
                session, user, payload["strategy_id"], payload["goal"], payload.get("budget", 10),
            )
            job.status = "completed"
            job.result_json = result
        else:
            job.status = "failed"
            job.result_json = {"error": f"unknown job type {job.type!r}"}
    except Exception as exc:  # noqa: BLE001 — a failed job must not crash the poller
        log.exception("job %s failed", job.id)
        session.rollback()  # discard any partial, uncommitted work from the failure point
        job = session.get(Job, job.id)  # re-fetch — rollback detaches the in-memory object
        job.status = "failed"
        job.result_json = {"error": str(exc)}
    job.finished_at = datetime.now(timezone.utc)
    session.commit()


def poll_jobs(session_factory: Callable[[], ContextManager[Session]] = get_session) -> None:
    """Called on a short interval by worker/scheduler.py. Claims and runs at
    most one job per call — sequential, matching wallet_runner.py's
    deliberate one-job-type-at-a-time simplicity (see the M9 plan)."""
    with session_factory() as session:
        job = claim_next_job(session)
        if job is None:
            return
        job_id = job.id

    with session_factory() as session:
        job = session.get(Job, job_id)
        run_job(session, job)
