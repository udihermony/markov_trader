"""Golden-file regression test for the backtest engine — the permanent
parity oracle DESIGN.md's M3 gate called for ("the POC's SMA crossover
strategy, re-expressed as a node graph, produces byte-identical backtest
results against the same data"). `legacy/` served that role for M1/M3 but
is retired now that this test exists: it runs the canonical SMA-crossover
graph (backend/engine/cli.py's defaults: fast=10, slow=20, max_hold_days=5,
cash_fraction=0.10) over a fixed, committed price fixture
(tests/golden/price_fixture.csv — no network, no yfinance) and compares
the resulting fills and final metrics against a committed golden file
(tests/golden/expected_backtest.json), byte for byte.

Only numbers are compared — `reason` strings are node-vocabulary labels,
not economics, and are allowed to change (e.g. a future node rename)
without this test flagging a false regression.

If a change to the engine or its defaults legitimately changes the
numbers, regenerate the golden file deliberately:

    python -m tests.test_backtest_golden --write-golden

and review the diff like any other change to committed behavior — this
test failing is a signal to look, not to blindly regenerate.
"""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import date
from pathlib import Path

from sqlalchemy import select

import backend.engine.graph.nodes  # noqa: F401  registers the node type library
from backend.db.models import EquitySnapshot, Fill, Instrument, PriceBar, ScreenResult
from backend.engine.backtest_runner import compute_metrics
from backend.engine.graph.compiled import CompiledGraph
from backend.engine.graph.spec import NodeSpec, SourceRef, StrategySpec
from backend.engine.orchestrator import Orchestrator
from backend.engine.sandbox import CostsConfig, Sandbox, SizingConfig
from backend.sources.finviz_screen import FinvizScreenAdapter, FinvizScreenSource, ScreenerConfig
from backend.sources.price_bars import DataConfig, PriceBarsFeatureAdapter, PriceBarsSource
from backend.sources.registry import SourceRegistry

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
PRICE_FIXTURE = GOLDEN_DIR / "price_fixture.csv"
EXPECTED_FILE = GOLDEN_DIR / "expected_backtest.json"

# cli.py's own defaults — this is "the SMA-crossover-as-graph strategy,"
# not an arbitrary parameterization of it.
FAST, SLOW, MAX_HOLD_DAYS = 10, 20, 5
CASH_FRACTION = 0.10
INITIAL_CASH = 100_000.0
MAX_CONCURRENT_POSITIONS = 8
MIN_NOTIONAL = 500.0
SLIPPAGE_BPS = 5.0
MIN_HISTORY_DAYS = 25
TOP_N = 10
BENCHMARK = "SPY"


def load_fixture() -> dict[str, list[tuple[date, float, float, float, float, int]]]:
    """ticker -> [(date, open, high, low, close, volume), ...], sorted by date."""
    by_ticker: dict[str, list[tuple]] = defaultdict(list)
    with PRICE_FIXTURE.open(newline="") as f:
        for row in csv.DictReader(f):
            by_ticker[row["ticker"]].append((
                date.fromisoformat(row["date"]), float(row["open"]), float(row["high"]),
                float(row["low"]), float(row["close"]), int(row["volume"]),
            ))
    for rows in by_ticker.values():
        rows.sort(key=lambda r: r[0])
    return by_ticker


def run_backtest(db_session, wallet) -> dict:
    fixture = load_fixture()
    tickers = sorted(t for t in fixture if t != BENCHMARK)

    for ticker, rows in fixture.items():
        instrument = Instrument(ticker=ticker)
        db_session.add(instrument)
        db_session.flush()
        for d, o, h, low, c, v in rows:
            db_session.add(
                PriceBar(instrument_id=instrument.id, date=d, open=o, high=h, low=low, close=c, volume=v)
            )
    for ticker in tickers:
        for d, *_ in fixture[ticker]:
            db_session.add(ScreenResult(screen_date=d, ticker=ticker, rank=1, source="golden"))
    db_session.flush()

    data_cfg = DataConfig(min_history_days=MIN_HISTORY_DAYS, benchmark_ticker=BENCHMARK)
    sizing = SizingConfig(
        initial_cash=INITIAL_CASH, cash_fraction=CASH_FRACTION,
        max_concurrent_positions=MAX_CONCURRENT_POSITIONS, min_notional=MIN_NOTIONAL,
    )
    costs = CostsConfig(slippage_bps=SLIPPAGE_BPS)

    price_bars = PriceBarsSource(db_session, data_cfg)
    price_bars.refresh = lambda tickers_, as_of: None  # fixture is pre-seeded — no network, no yfinance
    screener = FinvizScreenSource(db_session, ScreenerConfig(top_n=TOP_N), mode="backtest")
    sandbox = Sandbox(db_session, wallet.id, sizing, costs)

    registry = SourceRegistry()
    registry.register(PriceBarsFeatureAdapter(price_bars))
    registry.register(FinvizScreenAdapter(screener))

    fast_expr, slow_expr = f"sma(px.close, {FAST})", f"sma(px.close, {SLOW})"
    spec = StrategySpec(
        name="sma-crossover-golden",
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

    days = sorted({d for rows in fixture.values() for d, *_ in rows})
    for d in days:
        orch.run_day(d, quiet=True)

    fills = db_session.execute(
        select(Fill).where(Fill.wallet_id == wallet.id).order_by(Fill.id)
    ).scalars().all()
    equity_rows = db_session.execute(
        select(EquitySnapshot).where(EquitySnapshot.wallet_id == wallet.id).order_by(EquitySnapshot.date)
    ).scalars().all()
    metrics = compute_metrics(equity_rows, fills, INITIAL_CASH)
    final = equity_rows[-1]

    return {
        "fills": [
            {
                "date": f.timestamp.date().isoformat(), "ticker": f.ticker, "action": f.action,
                "shares": f.shares, "fill_price": round(float(f.fill_price), 6),
            }
            for f in fills
        ],
        "final_total_equity": round(float(final.total_equity), 6),
        "final_benchmark_equity": round(float(final.benchmark_equity), 6),
        "max_drawdown_pct": round(metrics.max_drawdown_pct, 6),
    }


def test_backtest_matches_golden_file(db_session, wallet):
    actual = run_backtest(db_session, wallet)
    expected = json.loads(EXPECTED_FILE.read_text())

    assert len(actual["fills"]) == len(expected["fills"]), (
        f"fill count changed: {len(actual['fills'])} vs golden {len(expected['fills'])}"
    )
    for i, (a, e) in enumerate(zip(actual["fills"], expected["fills"])):
        # Only economics are compared — `reason` isn't serialized at all.
        assert a["date"] == e["date"], f"fill {i}: date {a['date']} != golden {e['date']}"
        assert a["ticker"] == e["ticker"], f"fill {i}: ticker mismatch"
        assert a["action"] == e["action"], f"fill {i}: action mismatch"
        assert a["shares"] == e["shares"], f"fill {i}: shares {a['shares']} != golden {e['shares']}"
        assert a["fill_price"] == e["fill_price"], (
            f"fill {i}: fill_price {a['fill_price']} != golden {e['fill_price']}"
        )

    assert actual["final_total_equity"] == expected["final_total_equity"]
    assert actual["final_benchmark_equity"] == expected["final_benchmark_equity"]
    assert actual["max_drawdown_pct"] == expected["max_drawdown_pct"]


if __name__ == "__main__":
    # `python -m tests.test_backtest_golden --write-golden` — regenerates the
    # golden file. Never run automatically by pytest; a deliberate, reviewed
    # action for when a change legitimately shifts the engine's output.
    import sys

    from sqlalchemy.orm import Session

    from backend.db.base import engine as db_engine
    from backend.db.models import User, Wallet

    if "--write-golden" not in sys.argv:
        print(__doc__)
        raise SystemExit(1)

    conn = db_engine.connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        user = User(email="golden-fixture@localhost", password_hash="!golden!")
        session.add(user)
        session.flush()
        wallet = Wallet(
            user_id=user.id, name="golden", initial_cash=INITIAL_CASH, cash=INITIAL_CASH,
            start_date=date(2026, 1, 5), status="active", is_benchmark=False,
        )
        session.add(wallet)
        session.flush()
        result = run_backtest(session, wallet)
        EXPECTED_FILE.write_text(json.dumps(result, indent=2) + "\n")
        print(f"wrote {EXPECTED_FILE} ({len(result['fills'])} fills)")
    finally:
        trans.rollback()
        conn.close()
