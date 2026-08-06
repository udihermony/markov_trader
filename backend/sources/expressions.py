"""Feature expression parser (DESIGN.md §4.1).

Grammar — restricted to exactly what DESIGN.md's examples show. No nesting,
no arithmetic operators; neither appears anywhere in the spec:

    expr      := raw_ref | func_call
    raw_ref   := IDENT "." IDENT              # e.g. px.close
    func_call := IDENT "(" arg ("," arg)* ")"  # e.g. sma(px.close, 20)
    arg       := raw_ref | NUMBER

Hand-rolled via `re` — the grammar is small enough that a parser-generator
library would be pure overhead.

Warmup convention: the smallest number of bars such that the function's
value at the last bar is fully determined by real data, no NaN-padding. For
`rsi`/`atr` that's `window + 1` (the first valid value needs `window` deltas
/ true ranges); for everything else it's `window`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Literal

import pandas as pd

from backend.sources.registry import SourceRegistry

_RAW_RE = re.compile(r"^(?P<alias>\w+)\.(?P<feature>\w+)$")
_CALL_RE = re.compile(r"^(?P<func>\w+)\((?P<args>.+)\)$")


def _sma(x: pd.Series, n: int) -> pd.Series:
    return x.rolling(n).mean()


def _ema(x: pd.Series, n: int) -> pd.Series:
    return x.ewm(span=n, adjust=False).mean()


def _pct_change(x: pd.Series, n: int) -> pd.Series:
    return x.pct_change(n)


def _zscore(x: pd.Series, n: int) -> pd.Series:
    roll = x.rolling(n)
    return (x - roll.mean()) / roll.std()


def _rank(x: pd.Series, n: int) -> pd.Series:
    """Rolling rank of the latest value within its own trailing window,
    1-indexed ascending. The only self-consistent single-series,
    per-candidate interpretation of DESIGN.md §4.1's `rank` — cross-sectional
    ranking across tickers is `rank_top_n`, a separate *node* type (§4.8,
    M3) operating over a whole candidate set, not a feature expression.
    DESIGN.md §10 open question 5 flags the vocabulary boundary as
    unsettled; this is a documented judgment call."""
    return x.rolling(n).apply(lambda w: pd.Series(w).rank().iloc[-1], raw=False)


def _rsi(x: pd.Series, n: int) -> pd.Series:
    delta = x.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(n).mean()
    avg_loss = loss.rolling(n).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _atr_series(adapter: object, ticker: str, as_of: date, lookback_days: int, n: int) -> pd.Series:
    """True Range needs high/low/close, not just one series — the one
    function that doesn't fit "one series + one window". Resolved by calling
    the adapter's generic get_series() three times (it's generic over any
    declared feature name, so no OHLC-specific adapter reach-through is
    needed) rather than expanding the grammar to carry multiple columns."""
    high = adapter.get_series("high", ticker, as_of, lookback_days)  # type: ignore[attr-defined]
    low = adapter.get_series("low", ticker, as_of, lookback_days)  # type: ignore[attr-defined]
    close = adapter.get_series("close", ticker, as_of, lookback_days)  # type: ignore[attr-defined]
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.rolling(n).mean()


FUNCTIONS = {
    "sma": _sma,
    "ema": _ema,
    "rolling_mean": _sma,  # distinct vocabulary entry per DESIGN.md, identical implementation
    "pct_change": _pct_change,
    "zscore": _zscore,
    "rank": _rank,
    "rsi": _rsi,
    "atr": None,  # special-cased in evaluate() — needs three series, not one
}

_WARMUP_OFFSET = {"rsi": 1, "atr": 1}


@dataclass(frozen=True)
class FeatureExpr:
    kind: Literal["raw", "call"]
    alias: str                     # "px" — resolved via source_aliases at evaluate() time
    feature: str                    # "close"
    func: str | None = None          # "sma", None for a bare raw_ref
    window: int | None = None         # the function's integer arg, when present

    def warmup_days(self) -> int:
        if self.kind == "raw":
            return 0
        assert self.window is not None
        return self.window + _WARMUP_OFFSET.get(self.func, 0)

    def evaluate(
        self,
        registry: SourceRegistry,
        source_aliases: dict[str, str],
        ticker: str,
        as_of: date,
        lookback_days: int | None = None,
    ) -> float:
        source_id = source_aliases[self.alias]
        _, adapter = registry.get(source_id)
        n = self.window or 0
        calendar_lookback = lookback_days if lookback_days is not None else max(n * 3 + 30, 30)

        if self.kind == "raw":
            series = adapter.get_series(self.feature, ticker, as_of, calendar_lookback)  # type: ignore[attr-defined]
            return float(series.iloc[-1]) if len(series) else float("nan")

        if self.func == "atr":
            result = _atr_series(adapter, ticker, as_of, calendar_lookback, n)
        else:
            series = adapter.get_series(self.feature, ticker, as_of, calendar_lookback)  # type: ignore[attr-defined]
            result = FUNCTIONS[self.func](series, n)
        return float(result.iloc[-1]) if len(result) else float("nan")


def parse_feature_expression(expr: str) -> FeatureExpr:
    expr = expr.strip()

    call_match = _CALL_RE.match(expr)
    if call_match:
        func = call_match.group("func")
        if func not in FUNCTIONS:
            raise ValueError(f"unknown feature function: {func!r}")
        args = [a.strip() for a in call_match.group("args").split(",")]
        if len(args) != 2:
            raise ValueError(f"{func} expects exactly 2 arguments (feature, window), got {len(args)}")
        raw_match = _RAW_RE.match(args[0])
        if not raw_match:
            raise ValueError(f"first argument to {func} must be a raw feature reference, got {args[0]!r}")
        try:
            window = int(args[1])
        except ValueError:
            raise ValueError(f"second argument to {func} must be an integer window, got {args[1]!r}") from None
        return FeatureExpr(
            kind="call", alias=raw_match.group("alias"), feature=raw_match.group("feature"),
            func=func, window=window,
        )

    raw_match = _RAW_RE.match(expr)
    if raw_match:
        return FeatureExpr(kind="raw", alias=raw_match.group("alias"), feature=raw_match.group("feature"))

    raise ValueError(f"could not parse feature expression: {expr!r}")
