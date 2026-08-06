"""Offline end-to-end test: multi-day backtest loop over synthetic data.

Covers acceptance criteria 1 (end-to-end run populates fills and
equity_snapshots) and 2 (run_day is idempotent within a day).
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func, select

from backend.db.models import EquitySnapshot, Fill, Instrument, Order, PriceBar, ScreenResult
from backend.engine.orchestrator import Orchestrator
from backend.engine.sandbox import CostsConfig, Sandbox, SizingConfig
from backend.engine.strategy import build_strategy
from backend.sources.finviz_screen import FinvizScreenSource, ScreenerConfig
from backend.sources.price_bars import DataConfig, PriceBarsSource


def weekdays(start: date, n: int) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def seed_prices(session, ticker: str, days: list[date], closes: list[float]) -> None:
    instrument = session.execute(
        select(Instrument).where(Instrument.ticker == ticker)
    ).scalar_one_or_none()
    if instrument is None:
        instrument = Instrument(ticker=ticker)
        session.add(instrument)
        session.flush()
    for d, c in zip(days, closes):
        session.add(
            PriceBar(
                instrument_id=instrument.id, date=d,
                open=c, high=c * 1.01, low=c * 0.99, close=c, volume=1_000_000,
            )
        )
    session.flush()


def seed_watchlist(session, ticker: str, days: list[date]) -> None:
    for d in days:
        session.add(ScreenResult(screen_date=d, ticker=ticker, rank=1, source="seed"))
    session.flush()


def build_system(session, wallet_row) -> Orchestrator:
    data_cfg = DataConfig()
    sizing = SizingConfig()
    costs = CostsConfig()
    price_bars = PriceBarsSource(session, data_cfg)
    price_bars.refresh = lambda tickers, as_of: None  # offline: cache is pre-seeded
    screener = FinvizScreenSource(session, ScreenerConfig(), mode="backtest")
    sandbox = Sandbox(session, wallet_row.id, sizing, costs)
    strategy = build_strategy("sma_crossover", {"fast": 10, "slow": 20, "max_hold_days": 5})
    return Orchestrator(session, wallet_row.id, data_cfg, sizing, price_bars, screener, sandbox, strategy)


def test_backtest_end_to_end(db_session, wallet):
    orch = build_system(db_session, wallet)
    days = weekdays(date(2026, 1, 5), 60)

    # AAA: dips then rallies → upward SMA cross mid-series → BUY, later time stop.
    aaa = [100.0] * 25 + [90.0] * 5 + [70.0 + 3 * i for i in range(30)]
    seed_prices(db_session, "AAA", days, aaa)
    # SPY benchmark: gentle rise.
    seed_prices(db_session, "SPY", days, [500 + i * 0.5 for i in range(60)])
    # Recorded watchlist for every day (replayed in backtest mode — no finviz).
    seed_watchlist(db_session, "AAA", days)

    for d in days[30:]:
        orch.run_day(d, quiet=True)

    trades = db_session.execute(
        select(func.count()).select_from(Fill).where(
            Fill.wallet_id == wallet.id, Fill.action.in_(["BUY", "SELL"])
        )
    ).scalar_one()
    perf = db_session.execute(
        select(func.count()).select_from(EquitySnapshot).where(EquitySnapshot.wallet_id == wallet.id)
    ).scalar_one()
    assert trades > 0, "expected at least one fill"
    assert perf == len(days[30:])
    # Benchmark equity recorded and positive.
    row = db_session.execute(
        select(EquitySnapshot.benchmark_equity)
        .where(EquitySnapshot.wallet_id == wallet.id)
        .order_by(EquitySnapshot.date.desc())
        .limit(1)
    ).scalar_one()
    assert row and row > 0


def test_run_day_idempotent(db_session, wallet):
    orch = build_system(db_session, wallet)
    days = weekdays(date(2026, 1, 5), 40)
    aaa = [100.0] * 25 + [90.0, 90.0, 130.0] + [131.0] * 12  # cross on day 28
    seed_prices(db_session, "AAA", days, aaa)
    seed_prices(db_session, "SPY", days, [500.0] * 40)
    seed_watchlist(db_session, "AAA", days)

    signal_day = days[27]
    fill_day = days[28]
    orch.run_day(signal_day, quiet=True)
    orch.run_day(signal_day, quiet=True)  # rerun same day: no duplicate queue
    pending = db_session.execute(
        select(func.count()).select_from(Order).where(
            Order.wallet_id == wallet.id, Order.status == "pending"
        )
    ).scalar_one()
    assert pending == 1

    orch.run_day(fill_day, quiet=True)
    orch.run_day(fill_day, quiet=True)  # rerun: no double execution
    buys = db_session.execute(
        select(func.count()).select_from(Fill).where(
            Fill.wallet_id == wallet.id, Fill.action == "BUY"
        )
    ).scalar_one()
    assert buys == 1
    # Fill happened at day t+1 open, not signal-day close (no look-ahead).
    fill_ts = db_session.execute(
        select(Fill.timestamp).where(Fill.wallet_id == wallet.id, Fill.action == "BUY")
    ).scalar_one()
    assert fill_ts.date() == fill_day
