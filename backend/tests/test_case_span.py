"""
Tests for app/normalise/case_span.py.
Owner: Dipen (normalise + models lane).

The umbrella rule is advisory, which is exactly why it needs pinning: a check
that only ever warns is a check nobody notices going wrong. These tests fix the
two halves of the rule, the boundary behaviour at each threshold, and the two
degenerate inputs that would otherwise flag everything (a median of zero) or
nothing (no closed case at all).

Pure functions, no database.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.normalise.case_span import (
    UMBRELLA_MIN_PRS,
    UMBRELLA_SPAN_MULTIPLE,
    CaseSpan,
    is_umbrella,
    median_span_days,
    span_days,
    summarise,
    threshold_from,
)


def _case(name: str, days: float | None, n_prs: int | None = 1) -> CaseSpan:
    return CaseSpan(work_item_id=name, days=days, n_prs=n_prs)


# --- span_days ------------------------------------------------------------


def test_span_days_counts_calendar_days():
    opened = datetime(2026, 1, 1, tzinfo=UTC)
    closed = datetime(2026, 1, 11, 12, tzinfo=UTC)
    assert span_days(opened, closed) == pytest.approx(10.5)


def test_span_days_is_none_for_an_open_case():
    assert span_days(datetime(2026, 1, 1, tzinfo=UTC), None) is None
    assert span_days(None, datetime(2026, 1, 1, tzinfo=UTC)) is None
    assert span_days(None, None) is None


def test_span_days_reports_a_negative_span_rather_than_hiding_it():
    """closed before opened is a data bug; check 2 is what catches it. This
    function must not launder it into a plausible-looking zero."""
    out = span_days(datetime(2026, 2, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC))
    assert out is not None and out < 0


# --- median ---------------------------------------------------------------


def test_median_ignores_open_cases():
    """An unfinished case has no duration. Counting it as short would drag the
    median down and flag ordinary cases as umbrellas."""
    cases = [_case("a", 2), _case("b", 4), _case("c", None), _case("d", None)]
    assert median_span_days(cases) == 3


def test_median_is_none_when_nothing_has_closed():
    assert median_span_days([_case("a", None), _case("b", None)]) is None
    assert median_span_days([]) is None


def test_threshold_scales_the_median():
    assert threshold_from(3.0, 10.0) == 30.0


def test_threshold_declines_to_judge_a_zero_median():
    """Ten times zero is zero, which would flag every case that lasted a
    second. Returning None makes the caller say so instead."""
    assert threshold_from(0.0, 10.0) is None
    assert threshold_from(None, 10.0) is None


# --- the rule itself ------------------------------------------------------


def test_a_long_span_is_an_umbrella():
    assert is_umbrella(_case("a", 40), threshold_days=30) is True


def test_the_span_boundary_is_strictly_greater():
    assert is_umbrella(_case("a", 30), threshold_days=30) is False
    assert is_umbrella(_case("a", 30.01), threshold_days=30) is True


def test_enough_prs_is_an_umbrella_however_short_the_span():
    """A `[1/12]` series merged in a week is still a work programme."""
    assert is_umbrella(_case("a", 1, n_prs=UMBRELLA_MIN_PRS), threshold_days=30) is True


def test_the_pr_boundary_is_greater_or_equal():
    assert is_umbrella(_case("a", 1, n_prs=UMBRELLA_MIN_PRS - 1), threshold_days=30) is False


def test_pr_count_still_fires_when_the_span_cannot_be_judged():
    """The validator reads work_item and passes n_prs=None; the mapper knows
    the count. Neither half depends on the other."""
    assert is_umbrella(_case("a", None, n_prs=20), threshold_days=None) is True
    assert is_umbrella(_case("a", None, n_prs=None), threshold_days=None) is False


def test_an_unknown_pr_count_never_flags():
    assert is_umbrella(_case("a", 5, n_prs=None), threshold_days=30) is False


# --- summarise ------------------------------------------------------------


def test_summarise_counts_both_halves_and_the_union():
    cases = [
        _case("short-1", 1),
        _case("short-2", 2),
        _case("short-3", 3),
        _case("long", 500),
        _case("wide", 2, n_prs=12),
        _case("both", 400, n_prs=9),
    ]
    report = summarise(cases, multiple=10.0, min_prs=8)

    assert report.n_cases == 6
    assert report.n_measurable == 6
    assert {c.work_item_id for c in report.over_span} == {"long", "both"}
    assert {c.work_item_id for c in report.over_pr_count} == {"wide", "both"}
    # "both" is in the union once, not twice.
    assert [c.work_item_id for c in report.umbrellas] == ["long", "both", "wide"]


def test_summarise_keeps_open_cases_in_the_denominator():
    """"3 of 100" must not quietly become "3 of 12"."""
    cases = [_case(f"open-{i}", None) for i in range(10)] + [
        _case("a", 1),
        _case("b", 100),
    ]
    report = summarise(cases, multiple=10.0)
    assert report.n_cases == 12
    assert report.n_measurable == 2


def test_summarise_is_order_independent():
    cases = [_case("a", 1), _case("b", 3), _case("c", 500), _case("d", 2, n_prs=9)]
    baseline = summarise(cases)
    for permutation in ([3, 2, 1, 0], [1, 3, 0, 2], [2, 0, 3, 1]):
        assert summarise([cases[i] for i in permutation]) == baseline


def test_summarise_sorts_longest_first_then_by_id():
    cases = [_case("zzz", 100), _case("aaa", 100), _case("mmm", 900), _case("x", 1)]
    report = summarise(cases, multiple=0.5)  # median 100 -> threshold 50
    assert [c.work_item_id for c in report.umbrellas] == ["mmm", "aaa", "zzz"]


def test_summarise_flags_nothing_when_every_case_is_typical():
    report = summarise([_case(str(i), 3 + i % 2) for i in range(20)])
    assert report.umbrellas == ()
    assert report.threshold_days is not None


def test_summarise_declines_to_judge_when_nothing_has_closed():
    """The state of the database before the PR mapper runs: provisional cases
    with an opened_at and no closed_at."""
    report = summarise([_case(str(i), None) for i in range(5)])
    assert report.unscalable is True
    assert report.median_days is None
    assert report.over_span == ()
    assert report.umbrellas == ()


def test_a_zero_median_does_not_flag_the_whole_table():
    cases = [_case(f"same-day-{i}", 0.0) for i in range(9)] + [_case("slow", 30.0)]
    report = summarise(cases)
    assert report.median_days == 0
    assert report.unscalable is True
    assert report.umbrellas == (), "a zero threshold must not flag every case"


def test_summarise_reports_the_thresholds_it_used():
    """Every consumer prints these; they cannot be left to the reader to guess."""
    report = summarise([_case("a", 2), _case("b", 4)], multiple=7.5, min_prs=3)
    assert report.multiple == 7.5
    assert report.min_prs == 3
    assert report.median_days == 3
    assert report.threshold_days == 22.5


def test_summarise_of_nothing_is_empty_not_an_error():
    report = summarise([])
    assert report.n_cases == 0 and report.umbrellas == () and report.unscalable


def test_top_truncates_without_reordering():
    cases = [_case(f"c{i}", 1000 - i) for i in range(20)]
    report = summarise(cases, multiple=0.5)
    assert [c.work_item_id for c in report.top(3)] == ["c0", "c1", "c2"]
    assert len(report.top(100)) == len(report.umbrellas)


def test_defaults_are_the_documented_ones():
    """These two numbers are judgement calls that appear on screen. Pinning
    them means changing one is a deliberate act with a failing test attached."""
    assert UMBRELLA_SPAN_MULTIPLE == 10.0
    assert UMBRELLA_MIN_PRS == 8
