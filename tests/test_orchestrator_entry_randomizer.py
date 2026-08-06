from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select

import backend.engine.graph.nodes  # noqa: F401  registers the node type library
from backend.db.models import Fill, Instrument, PriceBar, User, Wallet
from backend.engine.graph.compiled import CompiledGraph
from backend.engine.graph.spec import StrategySpec
from backend.engine.orchestrator import Orchestrator
from backend.engine.sandbox import CostsConfig, Sandbox, SizingConfig
from backend.sources.finviz_screen import FinvizScreenAdapter, FinvizScreenSource, ScreenerConfig
from backend.sources.price_bars import DataConfig, PriceBarsFeatureAdapter, PriceBarsSource
from backend.sources.registry import SourceRegistry

NEVER_TRIGGER_SPEC = {
    "spec_version": 2,
    "name": "Never triggers on its own",
    "sources": [{"id": "px", "type": "price_bars"}],
    "nodes": [
        {"id": "u1", "kind": "universe", "type": "manual_list", "params": {"tickers": ["TICK"]}},
        # `threshold` on an impossible condition — the graph itself never fires an entry,
        # so any BUY that happens can only have come from entry_randomizer.
        {"id": "t1", "kind": "trigger", "type": "threshold",
         "params": {"feature": "px.close", "op": "<", "value": -1}},
        {"id": "x1", "kind": "exit", "type": "never", "params": {}},
        {"id": "s1", "kind": "size", "type": "fixed_fraction", "params": {"fraction": 0.5}},
    ],
    "edges": [["u1", "t1"]],
}


def _seed_bars(db_session, ticker: str, through: date, n: int = 40) -> list[date]:
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


def _build_orchestrator(db_session, wallet_id, entry_randomizer=None) -> Orchestrator:
    data_cfg = DataConfig()
    sizing = SizingConfig(initial_cash=100_000.0)
    costs = CostsConfig()
    price_bars = PriceBarsSource(db_session, data_cfg)
    screener = FinvizScreenSource(db_session, ScreenerConfig(), mode="backtest")
    sandbox = Sandbox(db_session, wallet_id, sizing, costs)
    registry = SourceRegistry()
    registry.register(PriceBarsFeatureAdapter(price_bars))
    registry.register(FinvizScreenAdapter(screener))
    graph = CompiledGraph(StrategySpec.model_validate(NEVER_TRIGGER_SPEC), registry)
    return Orchestrator(
        db_session, wallet_id, data_cfg, sizing, price_bars, sandbox, graph,
        entry_randomizer=entry_randomizer,
    )


def _make_wallet(db_session) -> Wallet:
    user = User(email="randomizer@example.com", password_hash="x")
    db_session.add(user)
    db_session.flush()
    wallet = Wallet(
        user_id=user.id, name="Test Wallet", strategy_id=None,
        initial_cash=100_000.0, cash=100_000.0, start_date=date(2026, 1, 1),
        status="active", is_benchmark=False,
    )
    db_session.add(wallet)
    db_session.flush()
    return wallet


def test_default_behavior_unchanged_without_randomizer(db_session, monkeypatch):
    monkeypatch.setattr(PriceBarsSource, "refresh", lambda self, tickers, as_of: None)
    dates = _seed_bars(db_session, "TICK", date(2026, 3, 2))
    wallet = _make_wallet(db_session)
    orch = _build_orchestrator(db_session, wallet.id)  # entry_randomizer=None (default)

    orch.run_day(dates[-2], quiet=True)
    orch.run_day(dates[-1], quiet=True)

    fills = db_session.execute(select(Fill).where(Fill.wallet_id == wallet.id)).scalars().all()
    assert fills == []  # the impossible threshold never fires without randomization


def test_randomizer_drives_entries_when_set(db_session, monkeypatch):
    monkeypatch.setattr(PriceBarsSource, "refresh", lambda self, tickers, as_of: None)
    dates = _seed_bars(db_session, "TICK", date(2026, 3, 2))
    wallet = _make_wallet(db_session)
    orch = _build_orchestrator(db_session, wallet.id, entry_randomizer=lambda: True)

    orch.run_day(dates[-2], quiet=True)
    orch.run_day(dates[-1], quiet=True)

    fills = db_session.execute(select(Fill).where(Fill.wallet_id == wallet.id)).scalars().all()
    assert len(fills) == 1
    assert fills[0].action == "BUY"
    assert fills[0].reason == "luck_test_random_entry"
