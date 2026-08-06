from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from backend.db.models import Instrument, PriceBar
from backend.sources.expressions import FUNCTIONS, parse_feature_expression
from backend.sources.price_bars import DataConfig, PriceBarsFeatureAdapter, PriceBarsSource
from backend.sources.registry import SourceRegistry


def test_parse_raw_ref():
    e = parse_feature_expression("px.close")
    assert e.kind == "raw"
    assert e.alias == "px"
    assert e.feature == "close"
    assert e.func is None
    assert e.warmup_days() == 0


def test_parse_sma_call():
    e = parse_feature_expression("sma(px.close, 20)")
    assert e.kind == "call"
    assert e.alias == "px"
    assert e.feature == "close"
    assert e.func == "sma"
    assert e.window == 20
    assert e.warmup_days() == 20


def test_parse_zscore_call():
    e = parse_feature_expression("zscore(x.sentiment, 30)")
    assert e.alias == "x"
    assert e.feature == "sentiment"
    assert e.func == "zscore"
    assert e.warmup_days() == 30


def test_parse_pct_change_call():
    e = parse_feature_expression("pct_change(pm.prob, 5)")
    assert e.alias == "pm"
    assert e.feature == "prob"
    assert e.window == 5
    assert e.warmup_days() == 5


@pytest.mark.parametrize(
    "func,window,expected_warmup",
    [
        ("sma", 20, 20),
        ("ema", 12, 12),
        ("rolling_mean", 10, 10),
        ("pct_change", 5, 5),
        ("zscore", 30, 30),
        ("rank", 14, 14),
        ("rsi", 14, 15),   # +1: needs `window` deltas
        ("atr", 14, 15),   # +1: needs `window` true ranges
    ],
)
def test_warmup_days_per_function(func, window, expected_warmup):
    e = parse_feature_expression(f"{func}(px.close, {window})")
    assert e.warmup_days() == expected_warmup


def test_unknown_function_raises():
    with pytest.raises(ValueError, match="unknown feature function"):
        parse_feature_expression("bogus(px.close, 20)")


def test_wrong_arg_count_raises():
    with pytest.raises(ValueError, match="expects exactly 2 arguments"):
        parse_feature_expression("sma(px.close, 20, 5)")


def test_non_numeric_window_raises():
    with pytest.raises(ValueError, match="must be an integer window"):
        parse_feature_expression("sma(px.close, twenty)")


def test_malformed_expression_raises():
    with pytest.raises(ValueError, match="could not parse"):
        parse_feature_expression("not a valid expr!!")


def test_first_arg_must_be_raw_ref():
    with pytest.raises(ValueError, match="must be a raw feature reference"):
        parse_feature_expression("sma(20, 20)")


# --- vocabulary implementations, tested directly against synthetic series ---


def test_sma_implementation():
    x = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    result = FUNCTIONS["sma"](x, 3)
    assert result.iloc[-1] == pytest.approx((3 + 4 + 5) / 3)


def test_rolling_mean_is_alias_of_sma():
    assert FUNCTIONS["rolling_mean"] is FUNCTIONS["sma"]


def test_pct_change_implementation():
    x = pd.Series([100.0, 110.0, 121.0])
    result = FUNCTIONS["pct_change"](x, 1)
    assert result.iloc[-1] == pytest.approx(0.10)


def test_zscore_implementation():
    x = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    result = FUNCTIONS["zscore"](x, 5)
    # last value is the max of the window, so its z-score is positive
    assert result.iloc[-1] > 0


def test_rank_implementation():
    x = pd.Series([3.0, 1.0, 2.0, 5.0])
    result = FUNCTIONS["rank"](x, 4)
    # 5.0 is the largest in the trailing window of 4 -> rank 4 (1-indexed ascending)
    assert result.iloc[-1] == 4.0


def test_rsi_implementation_all_gains_is_100():
    x = pd.Series([float(i) for i in range(1, 20)])  # strictly increasing
    result = FUNCTIONS["rsi"](x, 14)
    assert result.iloc[-1] == pytest.approx(100.0)


# --- the M2 demo: sma(px.close, 20) resolves through the registry ---


def test_sma_resolves_through_registry_end_to_end(db_session):
    instrument = Instrument(ticker="AAPL")
    db_session.add(instrument)
    db_session.flush()
    closes = [100.0 + i for i in range(30)]  # 30 trading days, deterministic ramp
    start = date(2026, 1, 5)
    day, added = start, 0
    dates = []
    while added < len(closes):
        if day.weekday() < 5:
            dates.append(day)
            added += 1
        day += timedelta(days=1)
    for d, c in zip(dates, closes):
        db_session.add(
            PriceBar(instrument_id=instrument.id, date=d, open=c, high=c, low=c, close=c, volume=1000)
        )
    db_session.flush()

    as_of = dates[-1]
    price_bars = PriceBarsSource(db_session, DataConfig())
    adapter = PriceBarsFeatureAdapter(price_bars)
    reg = SourceRegistry()
    reg.register(adapter)

    expr = parse_feature_expression("sma(px.close, 20)")
    assert expr.warmup_days() == 20

    value = expr.evaluate(reg, {"px": "price_bars"}, "AAPL", as_of)
    expected = pd.Series(closes).rolling(20).mean().iloc[-1]
    assert value == pytest.approx(expected)
