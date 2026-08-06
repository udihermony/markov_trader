"""Crossover tests: cross-event detection (not level comparison) and the
time stop counted in trading days."""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from backend.engine.strategy import Action, Position
from backend.engine.strategy.sma_crossover import SmaCrossover


def make_bars(closes: list[float], start: date = date(2026, 1, 1)) -> pd.DataFrame:
    """Synthetic daily bars on consecutive weekdays."""
    dates, d = [], start
    while len(dates) < len(closes):
        if d.weekday() < 5:
            dates.append(d)
        d += timedelta(days=1)
    return pd.DataFrame(
        {"open": closes, "high": closes, "low": closes, "close": closes,
         "volume": [1_000_000] * len(closes)},
        index=dates,
    )


STRAT = SmaCrossover(fast=3, slow=5, max_hold_days=5)


def test_buy_on_upward_cross_event():
    # Flat then a jump: fast crosses above slow exactly on the last bar.
    closes = [100.0] * 10 + [90.0, 90.0, 130.0]
    bars = make_bars(closes)
    sig = STRAT.generate_signal(bars, None, bars.index[-1])
    assert sig.action is Action.BUY
    assert sig.reason == "crossover_buy"


def test_no_buy_when_fast_merely_above_slow():
    # Steady uptrend: fast has been above slow for many bars — a *level*
    # comparison would BUY here; a cross-event detector must HOLD.
    closes = [100 + i for i in range(15)]
    bars = make_bars(closes)
    sig = STRAT.generate_signal(bars, None, bars.index[-1])
    assert sig.action is Action.HOLD
    assert sig.reason == "no_signal"


def test_sell_on_downward_cross():
    closes = [100.0] * 10 + [110.0, 110.0, 80.0, 80.0]
    bars = make_bars(closes)
    pos = Position("AAA", 10, 100.0, bars.index[0])
    sig = STRAT.generate_signal(bars, pos, bars.index[-1])
    assert sig.action is Action.SELL
    assert sig.reason == "crossover_exit"


def test_time_stop_counts_trading_days():
    # 10 consecutive weekday bars, entry 5 trading days before as_of.
    closes = [100 + i * 0.01 for i in range(20)]  # gentle drift, no crosses
    bars = make_bars(closes)
    as_of = bars.index[-1]
    entry = bars.index[-6]  # exactly 5 trading days held
    pos = Position("AAA", 10, 100.0, entry)
    sig = STRAT.generate_signal(bars, pos, as_of)
    assert sig.action is Action.SELL
    assert sig.reason == "time_stop_exit"
    assert sig.metadata["days_held"] == 5


def test_no_time_stop_before_limit():
    closes = [100 + i * 0.01 for i in range(20)]
    bars = make_bars(closes)
    as_of = bars.index[-1]
    entry = bars.index[-5]  # only 4 trading days held
    pos = Position("AAA", 10, 100.0, entry)
    sig = STRAT.generate_signal(bars, pos, as_of)
    assert sig.action is Action.HOLD


def test_time_stop_ignores_weekends():
    # Entry on a Friday, as_of the following Thursday = 4 trading days
    # (Mon-Thu) but 6 calendar days. Calendar counting would fire the stop
    # with max_hold_days=5; trading-day counting must not.
    strat = SmaCrossover(fast=3, slow=5, max_hold_days=5)
    closes = [100 + i * 0.01 for i in range(20)]
    bars = make_bars(closes, start=date(2026, 1, 5))  # Monday
    fridays = [d for d in bars.index if d.weekday() == 4]
    entry = fridays[1]
    as_of_candidates = [d for d in bars.index if d > entry]
    as_of = as_of_candidates[3]  # 4th trading day after entry (Thursday)
    assert (as_of - entry).days >= 6
    pos = Position("AAA", 10, 100.0, entry)
    sig = strat.generate_signal(bars, pos, as_of)
    assert sig.action is Action.HOLD


def test_insufficient_history_holds():
    bars = make_bars([100.0] * 4)  # < slow + 1
    sig = STRAT.generate_signal(bars, None, bars.index[-1])
    assert sig.action is Action.HOLD
    assert sig.reason == "insufficient_history"
