"""Strategy protocol and shared datatypes. Strategies are stateless — all
position awareness arrives via the `position` argument."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Protocol

import pandas as pd


class Action(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass(frozen=True)
class Signal:
    action: Action
    reason: str
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Position:
    ticker: str
    shares: int
    entry_price: float
    entry_date: date


class Strategy(Protocol):
    def generate_signal(
        self,
        bars: pd.DataFrame,          # OHLCV up to and including as_of
        position: Position | None,
        as_of: date,
    ) -> Signal: ...


def build_strategy(name: str, params: dict) -> Strategy:
    if name == "sma_crossover":
        from backend.engine.strategy.sma_crossover import SmaCrossover

        return SmaCrossover(**params)
    raise ValueError(f"unknown strategy: {name}")
