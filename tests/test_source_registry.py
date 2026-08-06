from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd
import pytest

from backend.sources.registry import (
    AlignmentPolicy,
    FeatureSpec,
    SourceRegistry,
    SourceSpec,
    TrustClass,
)

FAKE_SPEC = SourceSpec(
    id="fake",
    features={"value": FeatureSpec("value", "float")},
    trust_class=TrustClass.POINT_IN_TIME,
    native_frequency="daily",
    alignment=AlignmentPolicy(native_frequency="daily"),
    coverage_note="test double",
)


@dataclass
class FakeAdapter:
    spec: SourceSpec = FAKE_SPEC

    def get_series(self, feature: str, ticker: str, as_of: date, lookback_days: int) -> pd.Series:
        return pd.Series([1.0], index=[as_of])


def test_register_then_get_roundtrips():
    reg = SourceRegistry()
    adapter = FakeAdapter()
    reg.register(adapter)
    spec, got = reg.get("fake")
    assert spec is FAKE_SPEC
    assert got is adapter


def test_get_unregistered_raises_key_error():
    reg = SourceRegistry()
    with pytest.raises(KeyError):
        reg.get("nonexistent")


def test_source_spec_fields_preserved():
    reg = SourceRegistry()
    reg.register(FakeAdapter())
    spec, _ = reg.get("fake")
    assert spec.trust_class is TrustClass.POINT_IN_TIME
    assert spec.alignment.native_frequency == "daily"
    assert spec.coverage_note == "test double"
    assert "value" in spec.features


def test_alignment_policy_daily_ok_intraday_not_implemented():
    policy = AlignmentPolicy(native_frequency="daily")
    policy.join_for("daily")  # no raise
    with pytest.raises(NotImplementedError):
        policy.join_for("intraday")
