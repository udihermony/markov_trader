"""Daily wallet execution — the plain-function analogue of a per-wallet
job (DESIGN.md §7: "each wallet is an independent job"). No jobs table:
failures are isolated by giving each wallet its own DB session plus a
try/except in `run_all_active_wallets`, not by persisted job state
(deferred to M9, where a UI first needs to read job status/progress)."""
from __future__ import annotations

import logging
from datetime import date
from typing import Callable, ContextManager

from sqlalchemy import select
from sqlalchemy.orm import Session

import backend.engine.graph.nodes  # noqa: F401  registers the node type library
from backend.db.models import Strategy, Wallet
from backend.db.session import get_session
from backend.engine.graph.compiled import CompiledGraph
from backend.engine.graph.spec import StrategySpec
from backend.engine.orchestrator import Orchestrator
from backend.engine.sandbox import CostsConfig, Sandbox, SizingConfig
from backend.sources.finviz_screen import FinvizScreenAdapter, FinvizScreenSource, ScreenerConfig
from backend.sources.price_bars import DataConfig, PriceBarsFeatureAdapter, PriceBarsSource
from backend.sources.registry import SourceRegistry

log = logging.getLogger(__name__)

# Matches the CLI's hardcoded defaults (backend/engine/cli.py) — M3 already
# decided not to move max_concurrent_positions/min_notional/slippage_bps
# enforcement into the size node's params; staying consistent here rather
# than reading them back out of spec_json.
DEFAULT_MAX_CONCURRENT_POSITIONS = 8
DEFAULT_MIN_NOTIONAL = 500.0
DEFAULT_SLIPPAGE_BPS = 5.0


def run_wallet_day(session: Session, wallet: Wallet, as_of: date | None = None) -> None:
    strategy = session.get(Strategy, wallet.strategy_id)
    if strategy is None:
        raise ValueError(f"wallet {wallet.id} has no strategy (strategy_id={wallet.strategy_id})")
    spec = StrategySpec.model_validate(strategy.spec_json)

    data_cfg = DataConfig()
    sizing = SizingConfig(
        initial_cash=float(wallet.initial_cash),
        max_concurrent_positions=DEFAULT_MAX_CONCURRENT_POSITIONS,
        min_notional=DEFAULT_MIN_NOTIONAL,
    )
    costs = CostsConfig(slippage_bps=DEFAULT_SLIPPAGE_BPS)

    price_bars = PriceBarsSource(session, data_cfg)
    screener = FinvizScreenSource(session, ScreenerConfig(), mode="paper")
    sandbox = Sandbox(session, wallet.id, sizing, costs)

    # One fresh registry per call — no global registration pollution across
    # wallets/runs (same pattern cli.py and the M2/M3 tests already use).
    registry = SourceRegistry()
    registry.register(PriceBarsFeatureAdapter(price_bars))
    registry.register(FinvizScreenAdapter(screener))
    graph = CompiledGraph(spec, registry)

    orch = Orchestrator(session, wallet.id, data_cfg, sizing, price_bars, sandbox, graph)
    orch.run_day(as_of or date.today(), quiet=True)


def run_all_active_wallets(
    session_factory: Callable[[], ContextManager[Session]] = get_session,
) -> None:
    """Iterates every active wallet, running each one in its own fresh
    session so a failure in one doesn't roll back or block another's
    already-committed work. `session_factory` defaults to the real
    `get_session` (a new SessionLocal() per call) but is injectable so
    tests can substitute a single shared, uncommitted transactional session
    — `get_session()`'s real connections wouldn't see a test's unflushed
    seed data across separate connections."""
    with session_factory() as session:
        wallet_ids = list(
            session.execute(select(Wallet.id).where(Wallet.status == "active")).scalars()
        )

    for wallet_id in wallet_ids:
        try:
            with session_factory() as session:
                wallet = session.get(Wallet, wallet_id)
                run_wallet_day(session, wallet)
        except Exception:  # noqa: BLE001
            log.exception("wallet %s run failed; continuing with the rest", wallet_id)
