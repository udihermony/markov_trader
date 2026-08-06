"""Sandbox accounting tests: cash conservation, slippage direction,
rejection paths, no-pyramiding."""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from backend.db.models import SkippedSignal
from backend.engine.sandbox import CostsConfig, Sandbox, SizingConfig

AS_OF = date(2026, 1, 15)


@pytest.fixture()
def sandbox(db_session, wallet) -> Sandbox:
    return Sandbox(
        db_session,
        wallet_id=wallet.id,
        sizing=SizingConfig(initial_cash=100_000.0, cash_fraction=0.10,
                             max_concurrent_positions=2, min_notional=500.0),
        costs=CostsConfig(slippage_bps=5.0),
    )


def test_buy_slippage_against_buyer(sandbox):
    res = sandbox.execute_buy("AAA", 10_000, 100.0, AS_OF, "crossover_buy")
    assert res.executed
    assert res.fill_price == pytest.approx(100.0 * 1.0005)  # buys fill higher


def test_sell_slippage_against_seller(sandbox):
    sandbox.execute_buy("AAA", 10_000, 100.0, AS_OF, "crossover_buy")
    res = sandbox.execute_sell("AAA", 110.0, AS_OF, "crossover_exit")
    assert res.executed
    assert res.fill_price == pytest.approx(110.0 * 0.9995)  # sells fill lower


def test_cash_conservation_round_trip(sandbox):
    start_cash = sandbox.cash
    buy = sandbox.execute_buy("AAA", 10_000, 100.0, AS_OF, "crossover_buy")
    assert sandbox.cash == pytest.approx(start_cash - buy.shares * buy.fill_price)
    sell = sandbox.execute_sell("AAA", 100.0, AS_OF, "crossover_exit")
    expected = start_cash - buy.shares * buy.fill_price + sell.shares * sell.fill_price
    assert sandbox.cash == pytest.approx(expected)
    # Round trip at the same price must lose exactly the slippage.
    assert sandbox.cash < start_cash


def test_whole_shares_floored(sandbox):
    res = sandbox.execute_buy("AAA", 1_050, 100.0, AS_OF, "crossover_buy")
    assert res.shares == 10  # 1050 / 100.05 = 10.49 → 10


def test_no_pyramiding(sandbox):
    sandbox.execute_buy("AAA", 10_000, 100.0, AS_OF, "crossover_buy")
    res = sandbox.execute_buy("AAA", 10_000, 100.0, AS_OF, "crossover_buy")
    assert not res.executed
    assert res.reason == "already_held"
    skip = sandbox.session.execute(
        select(SkippedSignal).where(SkippedSignal.wallet_id == sandbox.wallet_id)
    ).scalar_one()
    assert skip.reason == "already_held"


def test_min_notional_rejection(sandbox):
    res = sandbox.execute_buy("AAA", 400, 100.0, AS_OF, "crossover_buy")
    assert not res.executed
    assert res.reason == "min_notional"
    assert sandbox.cash == pytest.approx(100_000.0)  # untouched


def test_insufficient_cash_rejection(sandbox):
    res = sandbox.execute_buy("AAA", 50, 100.0, AS_OF, "crossover_buy")
    assert not res.executed
    assert res.reason == "insufficient_cash"


def test_max_positions_rejection(sandbox):
    sandbox.execute_buy("AAA", 10_000, 100.0, AS_OF, "crossover_buy")
    sandbox.execute_buy("BBB", 10_000, 100.0, AS_OF, "crossover_buy")
    res = sandbox.execute_buy("CCC", 10_000, 100.0, AS_OF, "crossover_buy")
    assert not res.executed
    assert res.reason == "max_positions"


def test_sell_without_position_skipped(sandbox):
    res = sandbox.execute_sell("ZZZ", 100.0, AS_OF, "crossover_exit")
    assert not res.executed
    assert res.reason == "no_position"


def test_sell_is_full_liquidation(sandbox):
    sandbox.execute_buy("AAA", 10_000, 100.0, AS_OF, "crossover_buy")
    sandbox.execute_sell("AAA", 105.0, AS_OF, "crossover_exit")
    assert sandbox.get_open_positions() == []


def test_portfolio_value_marks_to_market(sandbox):
    buy = sandbox.execute_buy("AAA", 10_000, 100.0, AS_OF, "crossover_buy")
    value = sandbox.get_portfolio_value({"AAA": 110.0})
    assert value == pytest.approx(sandbox.cash + buy.shares * 110.0)
