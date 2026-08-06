"""Ephemeral backtests for the Lab (M7) — run the real engine, keep none of
it.

CLAUDE.md rule 7: "Wallets are forward-only... A wallet cannot be backdated
— that would be a backtest in disguise." A Lab experiment IS a backtest, so
it must never leave a Wallet/Order/Fill/Position/EquitySnapshot row behind.
Rather than build a second, parallel accounting engine (risking numbers that
quietly diverge from what a real wallet would produce — the exact class of
bug M3's byte-identical parity test exists to catch), `run_ephemeral_backtest`
runs the real Orchestrator/Sandbox/CompiledGraph — the same code path
cli.py's `cmd_backtest` uses — against a throwaway wallet inside its own
connection and transaction, then always rolls back. This is the identical
mechanism tests/conftest.py's `db_session` fixture already relies on
(`join_transaction_mode="create_savepoint"`), just used at runtime instead
of in a test: the outer transaction is the "undo everything" boundary, and
Sandbox/Orchestrator's internal `session.commit()` calls only release and
reopen a savepoint within it.
"""
from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable

from sqlalchemy import select
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from backend.db.base import engine as _db_engine
from backend.db.models import EquitySnapshot, Fill, User, Wallet
from backend.engine.graph.compiled import CompiledGraph
from backend.engine.graph.spec import StrategySpec
from backend.engine.orchestrator import Orchestrator
from backend.engine.sandbox import CostsConfig, Sandbox, SizingConfig
from backend.sources.finviz_screen import FinvizScreenAdapter, FinvizScreenSource, ScreenerConfig
from backend.sources.price_bars import DataConfig, PriceBarsFeatureAdapter, PriceBarsSource
from backend.sources.registry import SourceRegistry

LAB_SYSTEM_USER_EMAIL = "lab@localhost"


@dataclass(frozen=True)
class BacktestMetrics:
    total_return_pct: float
    benchmark_return_pct: float | None
    max_drawdown_pct: float
    n_trades: int
    n_closed_trades: int
    hit_rate: float | None  # None when there are no closed round trips to grade
    avg_holding_days_calendar: float | None


@dataclass(frozen=True)
class EphemeralBacktestResult:
    metrics: BacktestMetrics
    equity_curve: list[dict] = field(default_factory=list)  # [{date, total_equity, benchmark_equity}]
    fills: list[dict] = field(default_factory=list)  # [{date, ticker, action, shares, fill_price, reason}]


def compute_metrics(
    equity_rows: list[EquitySnapshot], fills: list[Fill], initial_cash: float
) -> BacktestMetrics:
    """Pure function over already-fetched rows — the same math that lived
    inline in Orchestrator.print_backtest_summary, extracted so the CLI and
    the Lab can never silently disagree about what a number means."""
    if not equity_rows:
        return BacktestMetrics(0.0, None, 0.0, 0, 0, None, None)

    final = equity_rows[-1]
    total_return_pct = (float(final.total_equity) / initial_cash - 1) * 100
    benchmark_return_pct = (
        (float(final.benchmark_equity) / initial_cash - 1) * 100
        if final.benchmark_equity is not None
        else None
    )

    peak, max_dd = 0.0, 0.0
    for row in equity_rows:
        peak = max(peak, float(row.total_equity))
        if peak > 0:
            max_dd = max(max_dd, (peak - float(row.total_equity)) / peak)

    n_trades = len(fills)
    sells = [f for f in fills if f.action == "SELL"]
    wins = closed = 0
    total_hold = 0
    for s in sells:
        b = next(
            (
                f for f in reversed(fills)
                if f.action == "BUY" and f.ticker == s.ticker and f.timestamp <= s.timestamp
            ),
            None,
        )
        if b:
            closed += 1
            if float(s.fill_price) > float(b.fill_price):
                wins += 1
            total_hold += (s.timestamp.date() - b.timestamp.date()).days

    hit_rate = (wins / closed * 100) if closed else None
    avg_hold = (total_hold / closed) if closed else None

    return BacktestMetrics(
        total_return_pct=total_return_pct,
        benchmark_return_pct=benchmark_return_pct,
        max_drawdown_pct=max_dd * 100,
        n_trades=n_trades,
        n_closed_trades=closed,
        hit_rate=hit_rate,
        avg_holding_days_calendar=avg_hold,
    )


def run_ephemeral_backtest(
    spec: StrategySpec,
    start: date,
    end: date,
    *,
    sizing: SizingConfig | None = None,
    costs: CostsConfig | None = None,
    entry_randomizer: Callable[[], bool] | None = None,
    connection: Connection | None = None,
) -> EphemeralBacktestResult:
    """`connection` is injectable so tests can pass their own transactional
    `db_session`'s connection — the same reasoning as `wallet_runner.
    run_all_active_wallets`'s `session_factory` param: a brand-new connection
    can't see another connection's uncommitted, unflushed test seed data.
    When injected, this function nests a SAVEPOINT inside the caller's
    already-open transaction instead of opening its own — still always
    rolled back, still leaves the caller's own transaction untouched."""
    sizing = sizing or SizingConfig()
    costs = costs or CostsConfig()
    data_cfg = DataConfig()

    owns_connection = connection is None
    conn = connection or _db_engine.connect()
    outer = conn.begin() if owns_connection else conn.begin_nested()
    session: Session | None = None
    try:
        session = Session(bind=conn, join_transaction_mode="create_savepoint")

        user = session.execute(
            select(User).where(User.email == LAB_SYSTEM_USER_EMAIL)
        ).scalar_one_or_none()
        if user is None:
            user = User(email=LAB_SYSTEM_USER_EMAIL, password_hash="!lab-ephemeral-no-login!")
            session.add(user)
            session.flush()

        wallet = Wallet(
            user_id=user.id,
            name=f"lab-ephemeral-{uuid.uuid4().hex[:8]}",
            strategy_id=None,
            initial_cash=sizing.initial_cash,
            cash=sizing.initial_cash,
            start_date=start,
            status="active",
            is_benchmark=False,
        )
        session.add(wallet)
        session.flush()

        price_bars = PriceBarsSource(session, data_cfg)
        screener = FinvizScreenSource(session, ScreenerConfig(), mode="backtest")
        sandbox = Sandbox(session, wallet.id, sizing, costs)

        registry = SourceRegistry()
        registry.register(PriceBarsFeatureAdapter(price_bars))
        registry.register(FinvizScreenAdapter(screener))
        graph = CompiledGraph(spec, registry)

        orch = Orchestrator(
            session, wallet.id, data_cfg, sizing, price_bars, sandbox, graph,
            entry_randomizer=entry_randomizer,
        )

        benchmark = data_cfg.benchmark_ticker
        price_bars.refresh([benchmark], end)
        price_bars._refresh_one(  # noqa: SLF001 — same pattern cli.py's cmd_backtest uses
            benchmark, start - timedelta(days=data_cfg.fetch_window_days), end
        )
        days = price_bars.trading_days(benchmark, start, end)
        for day in days:
            orch.run_day(day, quiet=True)

        equity_rows = session.execute(
            select(EquitySnapshot)
            .where(EquitySnapshot.wallet_id == wallet.id)
            .order_by(EquitySnapshot.date)
        ).scalars().all()
        fill_rows = session.execute(
            select(Fill).where(Fill.wallet_id == wallet.id).order_by(Fill.id)
        ).scalars().all()

        metrics = compute_metrics(equity_rows, fill_rows, sizing.initial_cash)
        equity_curve = [
            {
                "date": r.date.isoformat(),
                "total_equity": float(r.total_equity),
                "benchmark_equity": float(r.benchmark_equity) if r.benchmark_equity is not None else None,
            }
            for r in equity_rows
        ]
        fills = [
            {
                "date": f.timestamp.date().isoformat(),
                "ticker": f.ticker,
                "action": f.action,
                "shares": f.shares,
                "fill_price": float(f.fill_price),
                "reason": f.reason,
            }
            for f in fill_rows
        ]

        return EphemeralBacktestResult(metrics=metrics, equity_curve=equity_curve, fills=fills)
    finally:
        if session is not None:
            session.close()
        outer.rollback()
        if owns_connection:
            conn.close()


def calibrated_entry_probability(n_real_trades: int, n_trading_days: int) -> float:
    """The luck test's null model: a coin flip calibrated so a shuffled run's
    *expected* entry count matches the real run's actual entry count, holding
    universe/exits/sizing/slippage constant (see backend/engine/orchestrator.py's
    entry_randomizer). Self-calibrating — no assumption about return shape."""
    if n_trading_days <= 0:
        return 0.0
    return min(1.0, n_real_trades / n_trading_days)


def luck_baseline(null_samples: list[float], k: int) -> float | None:
    """"If you try k variants, chance alone tends to produce something in the
    top 1/k of the noise" — the value at the (1 - 1/k) percentile of a null
    return distribution. Needs at least one null sample to mean anything."""
    if not null_samples or k <= 0:
        return None
    ordered = sorted(null_samples)
    idx = min(len(ordered) - 1, max(0, math.ceil((1 - 1 / k) * len(ordered)) - 1))
    return ordered[idx]
