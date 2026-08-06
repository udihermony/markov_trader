from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Iterator

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import create_engine
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from backend.db.models import User, Wallet

REPO_ROOT = Path(__file__).resolve().parent.parent


def _test_database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        raise RuntimeError("TEST_DATABASE_URL is not set — see .env.example")
    return url


@pytest.fixture(scope="session")
def pg_connection() -> Iterator[Connection]:
    url = _test_database_url()
    alembic_cfg = AlembicConfig(str(REPO_ROOT / "alembic.ini"))
    alembic_cfg.set_main_option("sqlalchemy.url", url)

    # env.py reads DATABASE_URL from the environment and takes precedence
    # over the programmatic config, so swap it for the duration of the
    # upgrade — same mechanism the CLI uses, kept consistent here.
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    try:
        command.upgrade(alembic_cfg, "head")
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous

    engine = create_engine(url, future=True)
    connection = engine.connect()
    yield connection
    connection.close()
    engine.dispose()


@pytest.fixture()
def db_session(pg_connection: Connection) -> Iterator[Session]:
    """One transaction per test, rolled back on teardown — Postgres-native
    equivalent of legacy's per-test tmp_path sqlite file."""
    transaction = pg_connection.begin()
    session = Session(bind=pg_connection, join_transaction_mode="create_savepoint")
    yield session
    session.close()
    transaction.rollback()


@pytest.fixture()
def wallet(db_session: Session) -> Wallet:
    user = User(email="test@example.com", password_hash="x")
    db_session.add(user)
    db_session.flush()
    w = Wallet(
        user_id=user.id,
        name="test-wallet",
        initial_cash=100_000.0,
        cash=100_000.0,
        start_date=date(2026, 1, 1),
        status="active",
        is_benchmark=False,
    )
    db_session.add(w)
    db_session.flush()
    return w
