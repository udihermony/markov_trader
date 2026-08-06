"""SMA crossover with time stop.

BUY on the cross *event* only: fast <= slow at t-1 AND fast > slow at t.
Comparing levels instead of detecting the cross is a classic bug — tests
cover this. SELL on downward cross, or after max_hold_days *trading* days
(counted from `bars`, not calendar days).
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from backend.engine.strategy import Action, Position, Signal


class SmaCrossover:
    def __init__(self, fast: int = 10, slow: int = 20, max_hold_days: int = 5):
        if fast >= slow:
            raise ValueError("fast SMA period must be < slow")
        self.fast = fast
        self.slow = slow
        self.max_hold_days = max_hold_days

    def generate_signal(
        self, bars: pd.DataFrame, position: Position | None, as_of: date
    ) -> Signal:
        closes = bars["close"]
        if len(closes) < self.slow + 1:
            return Signal(Action.HOLD, "insufficient_history")

        fast = closes.rolling(self.fast).mean()
        slow = closes.rolling(self.slow).mean()
        f_now, s_now = fast.iloc[-1], slow.iloc[-1]
        f_prev, s_prev = fast.iloc[-2], slow.iloc[-2]
        meta = {
            "fast_sma": round(float(f_now), 4),
            "slow_sma": round(float(s_now), 4),
            "as_of": as_of.isoformat(),
        }

        if position is not None:
            # Exit 1: downward cross event.
            if f_prev >= s_prev and f_now < s_now:
                return Signal(Action.SELL, "crossover_exit", meta)
            # Exit 2: time stop in trading days, counted from bars.
            held = sum(1 for d in bars.index if position.entry_date < d <= as_of)
            if held >= self.max_hold_days:
                return Signal(Action.SELL, "time_stop_exit", {**meta, "days_held": held})
            return Signal(Action.HOLD, "holding", meta)

        # Entry: upward cross event only (not level comparison).
        if f_prev <= s_prev and f_now > s_now:
            return Signal(Action.BUY, "crossover_buy", meta)
        return Signal(Action.HOLD, "no_signal", meta)
