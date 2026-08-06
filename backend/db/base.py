from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return url


def make_engine(url: str | None = None):
    return create_engine(url or database_url(), future=True)


engine = create_engine(
    os.environ.get("DATABASE_URL", "postgresql+psycopg2://swing:swing@localhost:5432/markov_trader"),
    future=True,
)
SessionLocal = sessionmaker(bind=engine, future=True, expire_on_commit=False)
