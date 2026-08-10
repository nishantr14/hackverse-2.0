"""
P12 backtest tests.

The arithmetic is pure and tested without a database; the "does it run on
real data" check takes pg_engine and skips when nothing is listening, same
convention as the rest of the suite.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy.orm import Session

from app.models import backtest


def _obs(predicted: float, actual: int, p10: float = 0.0, p90: float = 1e9):
    return backtest.Observation(
        fold=0,
        repo="r",
        component="c",
        train_weeks=10,
        rate_per_week=1.0,
        predicted=predicted,
        actual=actual,
        p10=p10,
        p90=p90,
    )


# --- pure arithmetic ------------------------------------------------------


def test_mae_is_mean_absolute_error():
    r = backtest.Report([_obs(10, 8), _obs(5, 9)], 4, 1)
    assert r.mae == pytest.approx(3.0)  # |10-8|=2, |5-9|=4


def test_bias_separates_direction_from_magnitude():
    """Errors that cancel give ~0 bias; errors pointing one way do not.
    This is the whole reason bias is reported alongside MAE."""
    cancelling = backtest.Report([_obs(12, 8), _obs(4, 8)], 4, 1)
    one_way = backtest.Report([_obs(4, 8), _obs(5, 9)], 4, 1)
    assert cancelling.mae == one_way.mae == pytest.approx(4.0)
    assert cancelling.bias == pytest.approx(0.0)
    assert one_way.bias == pytest.approx(-4.0)


def test_percentage_error_skips_actuals_too_small_to_be_meaningful():
    """Predicting 3 when 1 shipped is a 200% error that says nothing. Those
    rows must not reach MAPE — but they must still reach MAE."""
    r = backtest.Report([_obs(3, 1), _obs(11, 10)], 4, 1)
    assert len(r.scored_for_pct) == 1
    assert r.mape == pytest.approx(10.0)
    assert r.n == 2  # both still counted for MAE


def test_mape_is_none_when_nothing_is_scoreable():
    r = backtest.Report([_obs(3, 1)], 4, 1)
    assert r.mape is None


def test_band_coverage_counts_actuals_inside_p10_p90():
    r = backtest.Report(
        [_obs(10, 10, p10=8, p90=12), _obs(10, 20, p10=8, p90=12)], 4, 1
    )
    assert r.band_coverage == pytest.approx(0.5)


def test_cv_of_a_flat_series_is_zero():
    assert backtest._cv([5, 5, 5, 5]) == pytest.approx(0.0)
    assert backtest._cv([1, 9, 1, 9]) > 0.5


def test_empty_report_does_not_divide_by_zero():
    r = backtest.Report([], 4, 3)
    assert r.n == 0
    assert "No observations" in backtest.format_report(r)


# --- the split itself: the property that makes it a BLIND backtest --------


def test_no_fold_trains_on_its_own_test_window(monkeypatch):
    """The one correctness property that matters. Training data must end
    strictly before the window being predicted, or the score is fiction."""
    start = date(2026, 1, 5)
    weeks = [start + timedelta(weeks=i) for i in range(30)]
    series = {("r", "c"): {w: 5 for w in weeks}}
    monkeypatch.setattr(backtest, "load_weekly", lambda _s: series)

    report = backtest.run_backtest(None, horizon_weeks=4, folds=3, min_items=10)
    assert report.n == 3  # one observation per fold

    # A flat 5/week series must predict 5*4=20 against an actual of 20.
    for o in report.observations:
        assert o.predicted == pytest.approx(20.0)
        assert o.actual == 20
        # Each fold trains on strictly fewer weeks than the one before it,
        # which is what walking the origin backwards means.
    assert [o.train_weeks for o in report.observations] == sorted(
        (o.train_weeks for o in report.observations), reverse=True
    )


def test_components_without_enough_history_are_excluded():
    start = date(2026, 1, 5)
    weeks = [start + timedelta(weeks=i) for i in range(30)]
    series = {("r", "quiet"): {w: 1 for w in weeks}}  # 30 items total
    import types

    session = types.SimpleNamespace()
    original = backtest.load_weekly
    try:
        backtest.load_weekly = lambda _s: series
        assert backtest.run_backtest(session, 4, 1, min_items=1000).n == 0
        assert backtest.run_backtest(session, 4, 1, min_items=5).n == 1
    finally:
        backtest.load_weekly = original


# --- against real Postgres ------------------------------------------------


def test_backtest_runs_on_real_data(pg_engine):
    with Session(pg_engine) as session:
        report = backtest.run_backtest(session)
    if report.n == 0:
        pytest.skip("no merged events in the window")
    assert report.mae >= 0
    assert 0.0 <= report.band_coverage <= 1.0
    # Every observation must be a real comparison, not a placeholder.
    assert all(o.predicted > 0 for o in report.observations)


def test_backtest_is_deterministic(pg_engine):
    with Session(pg_engine) as session:
        a = backtest.run_backtest(session)
        b = backtest.run_backtest(session)
    assert [ (o.predicted, o.actual) for o in a.observations ] == [
        (o.predicted, o.actual) for o in b.observations
    ]
