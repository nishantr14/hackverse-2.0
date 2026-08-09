"""
Rate card and band inference.

The two are tested separately on purpose, because the product claim is that
they ARE separate: rates are public and cited, bands are inferred and
labelled. A test file that mixed them would be the first place that stopped
being true.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
import yaml
from sqlalchemy import text

from app.cost.band_inference import (
    BAND_BASIS,
    SWEEP,
    ActorFeatures,
    assign_band,
    load_thresholds,
    tenure_bucket,
)
from app.cost.rate_card import (
    BANDS,
    RATES_PATH,
    RateCardError,
    build_rates,
    citation,
    hourly_from_annual,
    load_config,
)


def config_with(**overrides):
    """A complete, valid config; overrides are merged into rate_card."""
    cfg = {
        "rate_card": {
            "source": {
                "publisher": "Example Comp Report",
                "url": "https://example.org/report",
                "retrieved": "2026-08-09",
            },
            "currency": "INR",
            "loading": 1.30,
            "working_days": 230,
            "hours_per_day": 8,
            "bands": {
                "junior": {"annual": 800000},
                "mid": {"annual": 1600000},
                "senior": {"annual": 3000000},
                "staff": {"annual": 5000000},
            },
        }
    }
    cfg["rate_card"].update(overrides)
    return cfg


# --- the citation ---------------------------------------------------------


def test_a_complete_source_renders_as_one_string():
    note = citation(config_with())
    assert "https://example.org/report" in note
    assert "2026-08-09" in note


@pytest.mark.parametrize("field", ["publisher", "url", "retrieved"])
def test_an_empty_source_field_fails_loudly_and_by_name(field):
    """This is the check that stops a rupee figure reaching the screen with
    nothing behind it. It must not warn, and it must say which field."""
    cfg = config_with()
    cfg["rate_card"]["source"][field] = ""
    with pytest.raises(RateCardError, match=field):
        citation(cfg)


@pytest.mark.parametrize("placeholder", ["TODO", "tbd", "FILL ME", "change-me"])
def test_placeholder_text_is_not_a_citation(placeholder):
    """A source reading 'TODO' is worse than a blank one — it looks like a
    citation from the distance a judge is standing at."""
    cfg = config_with()
    cfg["rate_card"]["source"]["url"] = placeholder
    with pytest.raises(RateCardError, match="url"):
        citation(cfg)


def test_a_malformed_retrieval_date_is_refused():
    cfg = config_with()
    cfg["rate_card"]["source"]["retrieved"] = "last tuesday"
    with pytest.raises(RateCardError, match="YYYY-MM-DD"):
        citation(cfg)


def test_the_citation_is_checked_before_any_number_is_computed():
    """Order matters: a run that prints rates and THEN fails has already put
    uncited numbers on someone's terminal."""
    cfg = config_with()
    cfg["rate_card"]["source"]["url"] = ""
    cfg["rate_card"]["bands"]["mid"]["annual"] = None
    with pytest.raises(RateCardError, match="url"):
        build_rates(cfg)


# --- the arithmetic -------------------------------------------------------


def test_hourly_is_annual_times_loading_over_working_hours():
    # 1,840,000 * 1.30 / (230 * 8) = 1,300.00
    assert hourly_from_annual(
        Decimal(1840000), Decimal("1.30"), 230, 8
    ) == Decimal("1300.00")


def test_loading_is_applied_not_ignored():
    plain = hourly_from_annual(Decimal(1000000), Decimal("1.00"), 230, 8)
    loaded = hourly_from_annual(Decimal(1000000), Decimal("1.30"), 230, 8)
    assert loaded == (plain * Decimal("1.3")).quantize(Decimal("0.01"))


@pytest.mark.parametrize(("days", "hours"), [(0, 8), (230, 0), (-1, 8)])
def test_a_zero_working_year_is_an_error_not_an_infinity(days, hours):
    with pytest.raises(RateCardError):
        hourly_from_annual(Decimal(1000000), Decimal("1.3"), days, hours)


def test_every_band_needs_a_figure_and_there_is_no_default():
    """A plausible-looking number with no source behind it is the worst thing
    this table could contain, so a missing one is an error, not a fallback."""
    cfg = config_with()
    cfg["rate_card"]["bands"]["senior"]["annual"] = None
    with pytest.raises(RateCardError, match="senior"):
        build_rates(cfg)


def test_bands_out_of_order_are_caught():
    """Two swapped figures produce a working rate card that prices senior
    work below junior and reads as plausible in a table."""
    cfg = config_with()
    cfg["rate_card"]["bands"]["senior"]["annual"] = 100000
    with pytest.raises(RateCardError, match="ascending"):
        build_rates(cfg)


def test_all_four_bands_are_built_and_carry_the_citation():
    rates = build_rates(config_with())
    assert [r.role_band for r in rates] == list(BANDS)
    assert all(r.source and r.hourly > 0 for r in rates)


# --- the shipped config ---------------------------------------------------


def test_the_shipped_config_parses_and_has_every_band():
    cfg = yaml.safe_load(RATES_PATH.read_text(encoding="utf-8"))
    assert set(cfg["rate_card"]["bands"]) == set(BANDS)


def test_the_shipped_config_refuses_to_seed_until_it_is_filled_in():
    """Ships with a blank citation deliberately. If this test ever starts
    failing, someone has filled it in — check the URL is real, then delete
    this test."""
    with pytest.raises(RateCardError, match="source is incomplete"):
        build_rates(load_config())


# --- band inference: the rule ---------------------------------------------


def features(**kw) -> ActorFeatures:
    base = {
        "actor_hash": "a" * 16,
        "tenure_months": 0.0,
        "reviews": 0,
        "merged": 0,
        "merges_performed": 0,
        "n_events": 0,
        "seen_in_jira": False,
        "first_ts": None,
    }
    return ActorFeatures(**(base | kw))


THRESHOLDS = {
    "staff": {"tenure_months": 24, "reviews": 100},
    "senior": {"tenure_months": 12, "merged": 30},
    "mid": {"tenure_months": 6},
}


@pytest.mark.parametrize(
    ("kw", "expected"),
    [
        ({"tenure_months": 30, "reviews": 120}, "staff"),
        ({"tenure_months": 30, "reviews": 99}, "mid"),      # reviews just short
        ({"tenure_months": 23, "reviews": 500}, "mid"),     # tenure just short
        ({"tenure_months": 18, "merged": 40}, "senior"),
        ({"tenure_months": 12, "merged": 30}, "senior"),    # both exactly on
        ({"tenure_months": 12, "merged": 29}, "mid"),
        ({"tenure_months": 6}, "mid"),
        ({"tenure_months": 5.9}, "junior"),
        ({}, "junior"),
    ],
)
def test_the_stated_rule(kw, expected):
    assert assign_band(features(**kw), THRESHOLDS) == expected


def test_every_condition_must_hold_not_just_one():
    """`staff` needs tenure AND reviews. An actor with 500 reviews over three
    months is prolific, not senior."""
    assert assign_band(features(tenure_months=3, reviews=500), THRESHOLDS) != "staff"


def test_the_most_senior_matching_band_wins():
    """A staff actor also satisfies mid. Order of evaluation decides, and it
    must run downwards."""
    assert assign_band(
        features(tenure_months=40, reviews=300, merged=99), THRESHOLDS
    ) == "staff"


@pytest.mark.parametrize(
    ("months", "bucket"),
    [(0, "lt_6m"), (5.9, "lt_6m"), (6, "6m_2y"), (23.9, "6m_2y"), (24, "gt_2y")],
)
def test_tenure_bucket_matches_the_schema_check(months, bucket):
    assert tenure_bucket(months) == bucket


def test_thresholds_come_from_config_not_code():
    loaded = load_thresholds(load_config())
    assert loaded["staff"]["tenure_months"] == 24
    assert "junior" not in loaded, "junior is the fallthrough"


def test_an_unknown_band_in_config_is_refused():
    with pytest.raises(ValueError, match="unknown band"):
        load_thresholds({"band_inference": {"thresholds": {"principal": {}}}})


def test_conditions_on_junior_are_refused():
    with pytest.raises(ValueError, match="fallthrough"):
        load_thresholds({"band_inference": {"thresholds": {"junior": {"reviews": 1}}}})


def test_every_sweep_knob_is_a_real_feature():
    """A typo in SWEEP would silently print a column of zeros."""
    for name in SWEEP:
        assert hasattr(features(), name), f"{name} is not an ActorFeatures field"


# --- the labelling claim --------------------------------------------------


def test_the_only_band_basis_this_module_knows_is_inferred():
    assert BAND_BASIS == "inferred"


def test_no_source_file_anywhere_writes_a_stated_band():
    """Decision #8. Nothing in our data says what anyone's actual role is, so
    'stated' must be unreachable — asserted against the source, not just
    against today's rows."""
    import re
    from pathlib import Path

    app = Path(__file__).resolve().parents[1] / "app"
    offenders = [
        f"{path.name}:{i}"
        for path in app.rglob("*.py")
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if re.search(r"band_basis\s*[=:]\s*[\"']stated[\"']", line)
    ]
    assert not offenders, f"band_basis='stated' written at {offenders}"


# --- against live Postgres ------------------------------------------------


def test_no_actor_is_marked_stated(pg_engine):
    with pg_engine.connect() as conn:
        assert conn.execute(
            text("SELECT count(*) FROM actor WHERE band_basis <> 'inferred'")
        ).scalar() == 0


def test_no_rate_card_row_has_an_empty_source(pg_engine):
    """The source renders on screen next to the money."""
    with pg_engine.connect() as conn:
        bad = conn.execute(
            text(
                "SELECT role_band FROM rate_card "
                "WHERE source IS NULL OR btrim(source) = ''"
            )
        ).scalars().all()
    assert not bad, f"uncited rate rows: {bad}"


def test_every_seeded_rate_is_positive_and_in_a_known_band(pg_engine):
    with pg_engine.connect() as conn:
        rows = conn.execute(text("SELECT role_band, hourly FROM rate_card")).all()
    for band, hourly in rows:
        assert band in BANDS
        assert hourly > 0
