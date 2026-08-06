from __future__ import annotations

import contextlib
from datetime import date, timedelta

from sqlalchemy import select

from backend.db.models import EquitySnapshot, Fill, Instrument, PriceBar, Strategy, User, Wallet
from backend.sources.price_bars import PriceBarsSource
from backend.worker import wallet_runner
from backend.worker.wallet_runner import run_all_active_wallets, run_wallet_day

ALWAYS_BUY_SPEC = {
    "spec_version": 2,
    "name": "Always Buy",
    "sources": [{"id": "px", "type": "price_bars"}],
    "nodes": [
        {"id": "u1", "kind": "universe", "type": "manual_list", "params": {"tickers": []}},  # filled in per-test
        {"id": "t1", "kind": "trigger", "type": "always", "params": {}},
        {"id": "x1", "kind": "exit", "type": "never", "params": {}},
        {"id": "s1", "kind": "size", "type": "fixed_fraction", "params": {"fraction": 0.5}},
    ],
    "edges": [["u1", "t1"]],
}


def make_wallet(db_session, ticker: str, cash: float = 100_000.0) -> Wallet:
    user = User(email=f"runner-{ticker.lower()}@example.com", password_hash="x")
    db_session.add(user)
    db_session.flush()
    spec = {**ALWAYS_BUY_SPEC, "nodes": [
        {**ALWAYS_BUY_SPEC["nodes"][0], "params": {"tickers": [ticker]}},
        *ALWAYS_BUY_SPEC["nodes"][1:],
    ]}
    strategy = Strategy(user_id=user.id, name="Always Buy", spec_json=spec, spec_version=2)
    db_session.add(strategy)
    db_session.flush()
    wallet = Wallet(
        user_id=user.id, name="Test Wallet", strategy_id=strategy.id,
        initial_cash=cash, cash=cash, start_date=date(2026, 1, 1), status="active", is_benchmark=False,
    )
    db_session.add(wallet)
    db_session.flush()
    return wallet


def seed_bars(db_session, ticker: str, through: date, n: int = 40) -> list[date]:
    instrument = Instrument(ticker=ticker)
    db_session.add(instrument)
    db_session.flush()
    dates: list[date] = []
    d = through - timedelta(days=n * 2)
    while len(dates) <= n:
        if d.weekday() < 5:
            dates.append(d)
        d += timedelta(days=1)
    for dt in dates:
        db_session.add(
            PriceBar(instrument_id=instrument.id, date=dt, open=100, high=101, low=99, close=100, volume=1000)
        )
    db_session.flush()
    return dates


def test_run_wallet_day_creates_fill_and_snapshot(db_session, monkeypatch):
    monkeypatch.setattr(PriceBarsSource, "refresh", lambda self, tickers, as_of: None)
    dates = seed_bars(db_session, "TICK", date(2026, 3, 2))
    wallet = make_wallet(db_session, "TICK")

    signal_day, fill_day = dates[-2], dates[-1]
    run_wallet_day(db_session, wallet, as_of=signal_day)
    run_wallet_day(db_session, wallet, as_of=fill_day)

    fills = db_session.execute(select(Fill).where(Fill.wallet_id == wallet.id)).scalars().all()
    assert len(fills) == 1
    assert fills[0].action == "BUY"
    assert fills[0].ticker == "TICK"

    snapshots = db_session.execute(
        select(EquitySnapshot).where(EquitySnapshot.wallet_id == wallet.id)
    ).scalars().all()
    assert len(snapshots) == 2


@contextlib.contextmanager
def _reuse_session(session):
    yield session


def test_run_all_active_wallets_isolates_failures(db_session, monkeypatch):
    monkeypatch.setattr(PriceBarsSource, "refresh", lambda self, tickers, as_of: None)
    dates_a = seed_bars(db_session, "TICKA", date(2026, 3, 2))
    seed_bars(db_session, "TICKB", date(2026, 3, 2))
    wallet_a = make_wallet(db_session, "TICKA")
    wallet_b = make_wallet(db_session, "TICKB")

    real_run_wallet_day = wallet_runner.run_wallet_day

    def flaky_run_wallet_day(session, wallet, as_of=None):
        if wallet.id == wallet_a.id:
            raise RuntimeError("boom")
        return real_run_wallet_day(session, wallet, as_of)

    monkeypatch.setattr(wallet_runner, "run_wallet_day", flaky_run_wallet_day)

    run_all_active_wallets(session_factory=lambda: _reuse_session(db_session))

    # wallet_a's failure didn't stop wallet_b from getting its snapshot.
    snapshots_b = db_session.execute(
        select(EquitySnapshot).where(EquitySnapshot.wallet_id == wallet_b.id)
    ).scalars().all()
    assert len(snapshots_b) == 1
    snapshots_a = db_session.execute(
        select(EquitySnapshot).where(EquitySnapshot.wallet_id == wallet_a.id)
    ).scalars().all()
    assert len(snapshots_a) == 0
