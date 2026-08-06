"""The as-of conformance suite, run against the two real M2 adapters, plus
the concrete form of DESIGN.md's "a deliberately broken adapter fails
conformance" demo criterion."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from backend.db.models import Instrument, PriceBar, ScreenResult
from backend.sources.conformance import AsOfViolation, assert_as_of_conformant
from backend.sources.finviz_screen import FinvizScreenSource, ScreenerConfig
from backend.sources.price_bars import DataConfig, PriceBarsSource

CUTOFF = date(2026, 1, 15)


def test_price_bars_conformant(db_session):
    instrument = Instrument(ticker="AAA")
    db_session.add(instrument)
    db_session.flush()

    def seed_prior():
        db_session.add(
            PriceBar(instrument_id=instrument.id, date=CUTOFF,
                      open=100, high=101, low=99, close=100, volume=1000)
        )
        db_session.flush()

    def seed_future():
        db_session.add(
            PriceBar(instrument_id=instrument.id, date=CUTOFF + timedelta(days=1),
                      open=200, high=201, low=199, close=200, volume=1000)
        )
        db_session.flush()

    price_bars = PriceBarsSource(db_session, DataConfig())

    def read_as_of(as_of):
        return price_bars.get_bars("AAA", as_of)

    def extract_max_date(bars):
        return max(bars.index) if len(bars) else None

    assert_as_of_conformant(
        seed_prior=seed_prior, seed_future=seed_future, read_as_of=read_as_of,
        cutoff=CUTOFF, extract_max_date=extract_max_date,
    )


def test_finviz_screen_conformant(db_session):
    ticker_dates: dict[str, date] = {}

    def seed_prior():
        db_session.add(ScreenResult(screen_date=CUTOFF, ticker="AAA", rank=1, source="test"))
        db_session.flush()
        ticker_dates["AAA"] = CUTOFF

    def seed_future():
        future = CUTOFF + timedelta(days=1)
        db_session.add(ScreenResult(screen_date=future, ticker="BBB", rank=1, source="test"))
        db_session.flush()
        ticker_dates["BBB"] = future

    # Backtest mode's as-of replay path — the actual thing being conformance-
    # tested. `get_watchlist` itself has a live-Finviz fallback that would
    # make this networked/flaky, so we call the as-of-bounded internal
    # method directly.
    screener = FinvizScreenSource(db_session, ScreenerConfig(), mode="backtest")

    def read_as_of(as_of):
        return screener._load_recorded_asof(as_of)  # noqa: SLF001

    def extract_max_date(tickers):
        dates = [ticker_dates[t] for t in tickers if t in ticker_dates]
        return max(dates) if dates else None

    assert_as_of_conformant(
        seed_prior=seed_prior, seed_future=seed_future, read_as_of=read_as_of,
        cutoff=CUTOFF, extract_max_date=extract_max_date,
    )


def test_conformance_suite_catches_violations():
    """The concrete, runnable form of DESIGN.md's M2 demo criterion: a
    deliberately broken adapter fails conformance."""
    data: list[date] = []

    def seed_prior():
        data.append(CUTOFF)

    def seed_future():
        data.append(CUTOFF + timedelta(days=1))

    def read_as_of_broken(as_of):
        return list(data)  # ignores `as_of` entirely — the bug under test

    def extract_max_date(rows):
        return max(rows) if rows else None

    with pytest.raises(AsOfViolation, match="leaked data"):
        assert_as_of_conformant(
            seed_prior=seed_prior, seed_future=seed_future, read_as_of=read_as_of_broken,
            cutoff=CUTOFF, extract_max_date=extract_max_date,
        )
