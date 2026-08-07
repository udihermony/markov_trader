"""The M3 parity gate, made permanent (DESIGN.md / CLAUDE.md: "the POC's SMA
crossover strategy, re-expressed as a node graph, produces byte-identical
backtest results against the same data. Until that passes, the engine
transplant is unproven and no later milestone should start.").

M1 and M3 verified this by hand once each (see their commit messages) and
recorded the numbers there — nothing has re-run it since, and seven
milestones have touched the engine in the meantime. This test runs the
*actual* legacy engine (legacy/orchestrator.py, sandbox.py,
strategy/sma_crossover.py — untouched, imported directly, not reimplemented)
side by side with the new graph engine over identical seeded data, and
diffs their real output on every run.

`tests/test_integration.py::build_system`'s docstring used to claim to be
this test ("the concrete byte-identical regression test DESIGN.md
describes") while only asserting trade count/snapshot count against the new
engine alone — it never touched legacy at all. That docstring has been
corrected; this file is the real thing.
"""
from __future__ import annotations

import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import select

import backend.engine.graph.nodes  # noqa: F401  registers the node type library
from backend.db.models import Instrument, PriceBar, ScreenResult
from backend.engine.graph.compiled import CompiledGraph
from backend.engine.graph.spec import NodeSpec, SourceRef, StrategySpec
from backend.engine.orchestrator import Orchestrator
from backend.engine.sandbox import CostsConfig, Sandbox, SizingConfig
from backend.sources.finviz_screen import FinvizScreenAdapter, FinvizScreenSource, ScreenerConfig
from backend.sources.price_bars import DataConfig, PriceBarsFeatureAdapter, PriceBarsSource
from backend.sources.registry import SourceRegistry

LEGACY_ROOT = Path(__file__).resolve().parent.parent / "legacy"
sys.path.insert(0, str(LEGACY_ROOT))

from config import Config as LegacyConfig  # noqa: E402
from config import CostsConfig as LegacyCostsConfig  # noqa: E402
from config import DataConfig as LegacyDataConfig  # noqa: E402
from config import ScreenerConfig as LegacyScreenerConfig  # noqa: E402
from config import SizingConfig as LegacySizingConfig  # noqa: E402
from config import StrategyConfig as LegacyStrategyConfig  # noqa: E402
from data_provider import DataProvider as LegacyDataProvider  # noqa: E402
from db import get_connection as legacy_get_connection  # noqa: E402
from orchestrator import Orchestrator as LegacyOrchestrator  # noqa: E402
from sandbox import Sandbox as LegacySandbox  # noqa: E402
from screener import Screener as LegacyScreener  # noqa: E402
from strategy.sma_crossover import SmaCrossover  # noqa: E402

# Same parameters on both sides — this is "the same data, the same rules,"
# not a coincidence. Deliberately not config.yaml's values: this test's
# assumptions must be visible here, not inherited from a file that can
# drift independently of either engine.
FAST, SLOW, MAX_HOLD_DAYS = 10, 20, 5
CASH_FRACTION = 0.10
INITIAL_CASH = 100_000.0
MAX_CONCURRENT_POSITIONS = 8
MIN_NOTIONAL = 500.0
SLIPPAGE_BPS = 5.0
MIN_HISTORY_DAYS = 25
TICKER = "AAA"
BENCHMARK = "SPY"


def weekdays(start: date, n: int) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def price_series() -> list[float]:
    """Flat, then a dip, then a strong rally: guarantees an upward SMA
    cross (BUY) followed by enough sustained strength that the position
    exits via the 5-day time stop rather than a downward cross — exercising
    both a distinct entry path and a distinct exit path on both engines."""
    return [100.0] * 25 + [90.0] * 5 + [70.0 + 3 * i for i in range(40)]


def run_legacy(tmp_path) -> tuple:
    conn = legacy_get_connection(tmp_path / "legacy_parity.db")
    days = weekdays(date(2026, 1, 5), 70)
    closes = price_series()

    conn.executemany(
        "INSERT OR REPLACE INTO price_cache (ticker, date, open, high, low, close, volume) "
        "VALUES (?, ?, ?, ?, ?, ?, 1000000)",
        [(TICKER, d.isoformat(), c, c * 1.01, c * 0.99, c) for d, c in zip(days, closes)],
    )
    conn.executemany(
        "INSERT OR REPLACE INTO price_cache (ticker, date, open, high, low, close, volume) "
        "VALUES (?, ?, ?, ?, ?, ?, 1000000)",
        [(BENCHMARK, d.isoformat(), 500 + i * 0.5, 500 + i * 0.5, 500 + i * 0.5, 500 + i * 0.5)
         for i, d in enumerate(days)],
    )
    conn.executemany(
        "INSERT INTO watchlist_history (run_id, screen_date, ticker, rank) VALUES ('parity', ?, ?, 1)",
        [(d.isoformat(), TICKER) for d in days],
    )
    conn.commit()

    data_cfg = LegacyDataConfig(min_history_days=MIN_HISTORY_DAYS, benchmark_ticker=BENCHMARK)
    sizing_cfg = LegacySizingConfig(
        initial_cash=INITIAL_CASH, cash_fraction=CASH_FRACTION,
        max_concurrent_positions=MAX_CONCURRENT_POSITIONS, min_notional=MIN_NOTIONAL,
    )
    costs_cfg = LegacyCostsConfig(slippage_bps=SLIPPAGE_BPS)
    screener_cfg = LegacyScreenerConfig(filters={}, top_n=10)
    cfg = LegacyConfig(
        mode="backtest", db_path=tmp_path / "legacy_parity.db", screener=screener_cfg,
        data=data_cfg, strategy=LegacyStrategyConfig(), sizing=sizing_cfg, costs=costs_cfg,
    )

    provider = LegacyDataProvider(conn, data_cfg)
    provider.refresh = lambda tickers, as_of: None  # offline: cache is pre-seeded, no network
    screener = LegacyScreener(conn, screener_cfg, "parity", "backtest")
    sandbox = LegacySandbox(conn, "parity", "backtest", sizing_cfg, costs_cfg)
    strategy = SmaCrossover(fast=FAST, slow=SLOW, max_hold_days=MAX_HOLD_DAYS)
    orch = LegacyOrchestrator(cfg, provider, screener, sandbox, strategy, "parity")

    for d in days:
        orch.run_day(d, quiet=True)

    fills = conn.execute(
        "SELECT ticker, action, shares, fill_price FROM trade_log "
        "WHERE action IN ('BUY','SELL') ORDER BY id"
    ).fetchall()
    final = conn.execute(
        "SELECT total_equity, benchmark_equity FROM performance_history ORDER BY date DESC LIMIT 1"
    ).fetchone()
    return [dict(r) for r in fills], dict(final)


def run_new_engine(db_session, wallet) -> tuple:
    days = weekdays(date(2026, 1, 5), 70)
    closes = price_series()

    instrument = Instrument(ticker=TICKER)
    db_session.add(instrument)
    db_session.flush()
    for d, c in zip(days, closes):
        db_session.add(
            PriceBar(instrument_id=instrument.id, date=d, open=c, high=c * 1.01, low=c * 0.99, close=c,
                     volume=1_000_000)
        )
    spy = Instrument(ticker=BENCHMARK)
    db_session.add(spy)
    db_session.flush()
    for i, d in enumerate(days):
        p = 500 + i * 0.5
        db_session.add(
            PriceBar(instrument_id=spy.id, date=d, open=p, high=p, low=p, close=p, volume=1_000_000)
        )
    for d in days:
        db_session.add(ScreenResult(screen_date=d, ticker=TICKER, rank=1, source="parity"))
    db_session.flush()

    data_cfg = DataConfig(min_history_days=MIN_HISTORY_DAYS, benchmark_ticker=BENCHMARK)
    sizing = SizingConfig(
        initial_cash=INITIAL_CASH, cash_fraction=CASH_FRACTION,
        max_concurrent_positions=MAX_CONCURRENT_POSITIONS, min_notional=MIN_NOTIONAL,
    )
    costs = CostsConfig(slippage_bps=SLIPPAGE_BPS)

    price_bars = PriceBarsSource(db_session, data_cfg)
    price_bars.refresh = lambda tickers, as_of: None  # offline: cache is pre-seeded, no network
    screener = FinvizScreenSource(db_session, ScreenerConfig(top_n=10), mode="backtest")
    sandbox = Sandbox(db_session, wallet.id, sizing, costs)

    registry = SourceRegistry()
    registry.register(PriceBarsFeatureAdapter(price_bars))
    registry.register(FinvizScreenAdapter(screener))

    fast_expr = f"sma(px.close, {FAST})"
    slow_expr = f"sma(px.close, {SLOW})"
    spec = StrategySpec(
        name="sma-crossover-parity",
        sources=[SourceRef(id="px", type="price_bars")],
        nodes=[
            NodeSpec(id="u1", kind="universe", type="finviz_screen", params={}),
            NodeSpec(id="t1", kind="trigger", type="cross",
                     params={"a": fast_expr, "b": slow_expr, "direction": "up"}),
            NodeSpec(id="x1", kind="exit", type="cross",
                     params={"a": fast_expr, "b": slow_expr, "direction": "down"}),
            NodeSpec(id="x2", kind="exit", type="time_stop",
                     params={"max_hold_days": MAX_HOLD_DAYS, "calendar_feature": "px.close"}),
            NodeSpec(id="s1", kind="size", type="fixed_fraction", params={"fraction": CASH_FRACTION}),
        ],
        edges=[["u1", "t1"]],
    )
    graph = CompiledGraph(spec, registry)
    orch = Orchestrator(db_session, wallet.id, data_cfg, sizing, price_bars, sandbox, graph)

    for d in days:
        orch.run_day(d, quiet=True)

    from backend.db.models import EquitySnapshot, Fill

    fills = db_session.execute(
        select(Fill.ticker, Fill.action, Fill.shares, Fill.fill_price)
        .where(Fill.wallet_id == wallet.id).order_by(Fill.id)
    ).all()
    final = db_session.execute(
        select(EquitySnapshot.total_equity, EquitySnapshot.benchmark_equity)
        .where(EquitySnapshot.wallet_id == wallet.id).order_by(EquitySnapshot.date.desc()).limit(1)
    ).one()
    return [{"ticker": r.ticker, "action": r.action, "shares": r.shares, "fill_price": float(r.fill_price)}
            for r in fills], {"total_equity": float(final.total_equity),
                              "benchmark_equity": float(final.benchmark_equity)}


def test_legacy_and_graph_engines_produce_byte_identical_trades(tmp_path, db_session, wallet):
    legacy_fills, legacy_final = run_legacy(tmp_path)
    new_fills, new_final = run_new_engine(db_session, wallet)

    assert len(legacy_fills) >= 2, "test data must produce at least one round trip"
    assert len(legacy_fills) == len(new_fills)

    for legacy_fill, new_fill in zip(legacy_fills, new_fills):
        assert legacy_fill["ticker"] == new_fill["ticker"]
        assert legacy_fill["action"] == new_fill["action"]
        assert legacy_fill["shares"] == new_fill["shares"]
        # Byte-identical fill prices — same formula (fill_price * (1 +/- bps/1e4)),
        # same inputs, so exact equality is the right assertion, not a tolerance.
        assert legacy_fill["fill_price"] == new_fill["fill_price"]
        # `reason` is deliberately not compared — M3's commit recorded the one
        # expected difference: cosmetic label changes (crossover_buy -> cross_up)
        # from the new node vocabulary, not a behavioral difference.

    assert legacy_final["total_equity"] == new_final["total_equity"]
    assert legacy_final["benchmark_equity"] == new_final["benchmark_equity"]
