from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session as OrmSession

from backend.db.models import Job, User
from backend.worker.jobs import claim_next_job, enqueue_job, run_job


def _make_user(db_session) -> User:
    user = User(email="jobqueue@example.com", password_hash="x")
    db_session.add(user)
    db_session.flush()
    return user


def test_enqueue_creates_a_pending_job(db_session):
    user = _make_user(db_session)
    job = enqueue_job(db_session, user, "unattended_experiment_session", {"strategy_id": 1, "goal": "g"})

    assert job.status == "pending"
    assert job.type == "unattended_experiment_session"
    assert job.payload_json == {"strategy_id": 1, "goal": "g"}


def test_claim_next_job_marks_it_running_and_sets_started_at(db_session):
    user = _make_user(db_session)
    job = enqueue_job(db_session, user, "unattended_experiment_session", {})

    claimed = claim_next_job(db_session)

    assert claimed is not None
    assert claimed.id == job.id
    assert claimed.status == "running"
    assert claimed.started_at is not None


def test_claim_next_job_returns_none_when_nothing_pending(db_session):
    assert claim_next_job(db_session) is None


def test_claim_next_job_does_not_reclaim_an_already_running_job(db_session):
    user = _make_user(db_session)
    enqueue_job(db_session, user, "unattended_experiment_session", {})

    first = claim_next_job(db_session)
    second = claim_next_job(db_session)

    assert first is not None
    assert second is None


def test_run_job_unknown_type_fails_cleanly(db_session):
    user = _make_user(db_session)
    job = enqueue_job(db_session, user, "some_unknown_type", {})
    job = claim_next_job(db_session)

    run_job(db_session, job)

    assert job.status == "failed"
    assert "unknown job type" in job.result_json["error"]
    assert job.finished_at is not None


def test_run_job_dispatch_failure_lands_in_failed_not_a_crash(db_session, monkeypatch):
    import backend.worker.jobs as jobs_module

    def _boom(*args, **kwargs):
        raise RuntimeError("session exploded")

    monkeypatch.setattr(jobs_module, "run_unattended_session", _boom)

    user = _make_user(db_session)
    enqueue_job(db_session, user, "unattended_experiment_session", {"strategy_id": 1, "goal": "g"})
    job = claim_next_job(db_session)

    run_job(db_session, job)  # must not raise

    refreshed = db_session.get(Job, job.id)
    assert refreshed.status == "failed"
    assert "session exploded" in refreshed.result_json["error"]
    assert refreshed.finished_at is not None


def test_claim_next_job_skips_a_row_locked_by_another_connection(pg_connection):
    """A genuine two-connection test — proves `SKIP LOCKED` actually skips a
    row another in-flight transaction holds, rather than blocking on it or
    double-claiming it. Uses its own real connections/commits (not the
    savepoint-per-test `db_session` fixture, which a second connection
    can't see), with explicit cleanup since nothing here auto-rolls-back."""
    engine = pg_connection.engine

    setup_conn = engine.connect()
    setup_session = OrmSession(bind=setup_conn)
    user = User(email="jobqueue-concurrent@example.com", password_hash="x")
    setup_session.add(user)
    setup_session.flush()
    job_a = Job(user_id=user.id, type="unattended_experiment_session", payload_json={}, status="pending")
    job_b = Job(user_id=user.id, type="unattended_experiment_session", payload_json={}, status="pending")
    setup_session.add_all([job_a, job_b])
    setup_session.commit()

    locker_conn = engine.connect()
    claimer_conn = engine.connect()
    try:
        locker_txn = locker_conn.begin()
        locker_conn.execute(select(Job.id).where(Job.id == job_a.id).with_for_update())

        claimer_session = OrmSession(bind=claimer_conn)
        claimed = claim_next_job(claimer_session)

        assert claimed is not None
        assert claimed.id == job_b.id  # job_a was locked by `locker_conn` and skipped

        locker_txn.rollback()
    finally:
        locker_conn.close()
        claimer_conn.close()
        cleanup_conn = engine.connect()
        cleanup_conn.execute(delete(Job).where(Job.user_id == user.id))
        cleanup_conn.execute(delete(User).where(User.id == user.id))
        cleanup_conn.commit()
        cleanup_conn.close()
        setup_conn.close()
