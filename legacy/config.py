"""Typed configuration loaded from config.yaml (+ secrets from .env)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv


@dataclass
class ScreenerConfig:
    filters: dict[str, str]
    top_n: int = 10
    retries: int = 3
    backoff_seconds: float = 2.0
    timeout_seconds: float = 30.0


@dataclass
class DataConfig:
    fetch_window_days: int = 90
    min_history_days: int = 25
    refresh_last_bars: int = 1
    retries: int = 2
    backoff_seconds: float = 1.5
    benchmark_ticker: str = "SPY"


@dataclass
class StrategyConfig:
    name: str = "sma_crossover"
    params: dict = field(default_factory=dict)


@dataclass
class SizingConfig:
    initial_cash: float = 100_000.0
    cash_fraction: float = 0.10
    max_concurrent_positions: int = 8
    min_notional: float = 500.0


@dataclass
class CostsConfig:
    slippage_bps: float = 5.0


@dataclass
class Config:
    mode: str  # 'paper' | 'backtest'
    db_path: Path
    screener: ScreenerConfig
    data: DataConfig
    strategy: StrategyConfig
    sizing: SizingConfig
    costs: CostsConfig


def load_config(mode: str | None = None, path: str | Path = "config.yaml") -> Config:
    load_dotenv()  # secrets pattern; no keys required today
    path = Path(path)
    raw = yaml.safe_load(path.read_text())

    mode = mode or raw["run"]["default_mode"]
    if mode not in ("paper", "backtest"):
        raise ValueError(f"invalid mode: {mode}")

    db_key = "paper_path" if mode == "paper" else "backtest_path"
    db_path = path.parent / raw["database"][db_key]

    return Config(
        mode=mode,
        db_path=db_path,
        screener=ScreenerConfig(**raw["screener"]),
        data=DataConfig(**raw["data"]),
        strategy=StrategyConfig(**raw["strategy"]),
        sizing=SizingConfig(**raw["sizing"]),
        costs=CostsConfig(**raw["costs"]),
    )
