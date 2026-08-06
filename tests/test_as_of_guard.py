"""Acceptance criterion 3: no module can read price data past `as_of`."""
from __future__ import annotations

from datetime import date

from sqlalchemy import update
from sqlalchemy.orm import Session

from backend.db.models import Instrument, PriceBar
from backend.sources.price_bars import DataConfig, PriceBarsSource


def seed(session: Session, ticker: str, days: list[str]) -> int:
    instrument = Instrument(ticker=ticker)
    session.add(instrument)
    session.flush()
    for d in days:
        session.add(
            PriceBar(
                instrument_id=instrument.id,
                date=date.fromisoformat(d),
                open=100,
                high=101,
                low=99,
                close=100,
                volume=1000,
            )
        )
    session.flush()
    return instrument.id


def test_get_bars_never_returns_future_data(db_session):
    seed(db_session, "AAA", ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09"])
    provider = PriceBarsSource(db_session, DataConfig())

    bars = provider.get_bars("AAA", as_of=date(2026, 1, 7))
    assert len(bars) == 3
    assert max(bars.index) <= date(2026, 1, 7)


def test_get_close_respects_as_of(db_session):
    instrument_id = seed(db_session, "AAA", ["2026-01-05", "2026-01-09"])
    db_session.execute(
        update(PriceBar)
        .where(PriceBar.instrument_id == instrument_id, PriceBar.date == date(2026, 1, 9))
        .values(close=999)
    )
    db_session.flush()
    provider = PriceBarsSource(db_session, DataConfig())
    # as_of between the two bars → must return the earlier close, never 999.
    assert provider.get_close("AAA", date(2026, 1, 7)) == 100.0
