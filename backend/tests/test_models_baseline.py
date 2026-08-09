"""
P9 (cycle-time forecaster) and P10 (capability index) — BASELINE tests.

Both baselines are thin SQL over views that already exist in the frozen
schema, so the pure arithmetic is tested without a database and the
"is it actually reading history" checks take pg_engine and SKIP when
nothing is listening — same convention as test_process_and_waste.py.
"""

from __future__ import annotations

import dataclasses

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import capability_index, forecaster


# --- pure logic: no DB needed -------------------------------------------


def test_quantiles_are_ordered_and_interpolated():
    p10, p50, p90 = forecaster.quantiles([float(i) for i in range(1, 101)])
    assert p10 < p50 < p90
    assert p50 == pytest.approx(50.5)
    assert p10 == pytest.approx(10.9)
    assert p90 == pytest.approx(90.1)


def test_quantiles_refuse_to_invent_a_number_from_nothing():
    with pytest.raises(forecaster.NoHistoricalEvidence):
        forecaster.quantiles([])


def test_forecast_values_are_not_hardcoded():
    """Different history must produce different numbers — the check that
    catches a baseline quietly replaced by a demo constant."""
    slow = forecaster.quantiles([100.0, 200.0, 300.0])
    fast = forecaster.quantiles([1.0, 2.0, 3.0])
    assert slow != fast
    assert slow[1] == pytest.approx(200.0)
    assert fast[1] == pytest.approx(2.0)


def test_average_contribution_scores_neutral():
    """Four actors, each doing a quarter of the work, all score 1.0."""
    assert capability_index.score(5, 5, 20, 20, 4) == pytest.approx(1.0)


def test_capability_is_clamped_both_ways():
    hero = capability_index.score(100, 100, 100, 100, 10)  # one actor did everything
    ghost = capability_index.score(0, 0, 100, 100, 10)
    assert hero == capability_index.CLAMP[1]
    assert ghost == capability_index.CLAMP[0]


def test_capability_never_carries_an_identifier():
    """Structural: the return type cannot hold an actor identifier, so no
    code path can leak one into an API response by accident."""
    fields = {f.name for f in dataclasses.fields(capability_index.Capability)}
    assert not {"actor_hash", "actor", "login", "name"} & fields


def test_p11_can_import_and_call_both_interfaces():
    """The contract the simulator depends on: both callables exist with the
    agreed signatures, and both dataclasses expose the agreed fields."""
    assert callable(forecaster.forecast_cycle_time)
    assert callable(capability_index.get_capability)

    fc = {f.name for f in dataclasses.fields(forecaster.CycleTimeForecast)}
    assert {"p10_hours", "p50_hours", "p90_hours", "basis", "n_samples"} <= fc

    cap = {f.name for f in dataclasses.fields(capability_index.Capability)}
    assert {"effectiveness", "basis"} <= cap


def test_baseline_never_claims_to_be_lightgbm():
    src = (forecaster.__doc__ or "") + (forecaster.forecast_cycle_time.__doc__ or "")
    assert "NOT the LightGBM model" in src or "NOT THE LIGHTGBM" in src.upper()


# --- against real Postgres: skips when none is listening -----------------


def test_forecaster_reads_real_history(pg_engine):
    with Session(pg_engine) as s:
        n = s.execute(text("SELECT count(*) FROM v_cycle_time")).scalar_one()
        if n == 0:
            pytest.skip("v_cycle_time is empty — nothing merged in the event log yet")
        f = forecaster.forecast_cycle_time(s)
        assert f.n_samples > 0
        assert 0 < f.p10_hours <= f.p50_hours <= f.p90_hours
        assert f.basis.startswith("historical_quantiles")


def test_sparse_group_falls_back_and_says_so(pg_engine):
    """A component that cannot exist has no samples, so the forecaster must
    widen to the global population and record that it did."""
    with Session(pg_engine) as s:
        if s.execute(text("SELECT count(*) FROM v_cycle_time")).scalar_one() == 0:
            pytest.skip("v_cycle_time is empty")
        f = forecaster.forecast_cycle_time(s, component="__no_such_component__")
        assert f.basis == "historical_quantiles_global"
        assert any("widened" in a for a in f.assumptions)


def test_capability_falls_back_to_neutral_for_an_unknown_component(pg_engine):
    with Session(pg_engine) as s:
        cap = capability_index.get_capability(s, "0" * 16, "__no_such_component__")
        assert cap.effectiveness == capability_index.NEUTRAL
        assert cap.basis == "neutral_fallback"
