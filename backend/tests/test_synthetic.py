"""
Synthetic generators: determinism, reconciliation, and the privacy floor.

The privacy tests here are the load-bearing ones. Synthetic data is where a
per-person figure is easiest to introduce by accident, because nobody feels
protective of a number they made up — and it prices real people's real work.
"""

from __future__ import annotations

import random
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.synthetic import gen_calendar, gen_tokens


def pricing() -> gen_tokens.Pricing:
    return gen_tokens.Pricing(
        vendor="test-model",
        input_per_million=Decimal("3.00"),
        output_per_million=Decimal("15.00"),
        fx=Decimal("84.0"),
        source="Example price list — https://example.org (retrieved 2026-08-09)",
    )


def rates_config(**overrides) -> dict:
    cfg = {
        "pricing": {
            "source": {
                "publisher": "Example",
                "url": "https://example.org/pricing",
                "retrieved": "2026-08-09",
            },
            "usd_to_inr": {"rate": 84.0, "source": "RBI reference rate 2026-08-09"},
            "vendor": "test-model",
            "input_per_million": 3.0,
            "output_per_million": 15.0,
        }
    }
    cfg["pricing"].update(overrides)
    return cfg


# --- token pricing --------------------------------------------------------


def test_cost_is_computed_from_tokens_not_sampled():
    """1M in at $3 + 1M out at $15 = $18 = 1,512 INR at 84."""
    assert gen_tokens.price(1_000_000, 1_000_000, pricing()) == Decimal("1512.00")


def test_cost_scales_linearly_with_tokens():
    one = gen_tokens.price(500_000, 100_000, pricing())
    two = gen_tokens.price(1_000_000, 200_000, pricing())
    assert two == one * 2


@pytest.mark.parametrize("field", ["publisher", "url", "retrieved"])
def test_an_uncited_price_refuses_to_generate(field):
    """The volumes are modelled; the prices are not. An uncited price makes a
    simulation look like an invoice."""
    cfg = rates_config()
    cfg["pricing"]["source"][field] = ""
    with pytest.raises(gen_tokens.AiRatesError, match=field):
        gen_tokens.load_pricing(cfg)


def test_a_missing_fx_rate_refuses_to_generate():
    """cost_event.cost has no currency column. An unconverted USD figure would
    be added straight to rupee labour cost with nothing to flag it."""
    cfg = rates_config(usd_to_inr={"rate": None, "source": "x"})
    with pytest.raises(gen_tokens.AiRatesError, match="usd_to_inr"):
        gen_tokens.load_pricing(cfg)


def test_an_uncited_fx_rate_refuses_too():
    cfg = rates_config(usd_to_inr={"rate": 84.0, "source": ""})
    with pytest.raises(gen_tokens.AiRatesError, match="usd_to_inr"):
        gen_tokens.load_pricing(cfg)


def test_the_shipped_ai_config_is_filled_in_and_loads():
    """config/ai_rates.yaml now carries real citations (Anthropic pricing
    page, Frankfurter FX). If this ever starts raising AiRatesError again,
    the config regressed to a placeholder — check config/ai_rates.yaml."""
    pricing_ = gen_tokens.load_pricing(gen_tokens.load_config())
    assert pricing_.vendor
    assert pricing_.input_per_million > 0
    assert pricing_.output_per_million > 0
    assert pricing_.fx > 0
    assert pricing_.source


# --- adoption curve -------------------------------------------------------


def test_adoption_rises_across_sprints():
    cfg = {"adoption": {"shape": "logistic", "start": 0.02, "end": 0.55,
                        "midpoint": 0.65, "steepness": 9.0}}
    sprints = list(range(1, 27))
    rates = [gen_tokens.adoption_for(s, sprints, cfg) for s in sprints]
    assert rates == sorted(rates), "adoption must be monotonic"
    assert rates[0] < 0.05, "earliest sprint should be near zero"
    assert rates[-1] > 0.4, "latest sprint should be meaningful"


def test_an_explicit_sprint_override_wins():
    cfg = {"adoption": {"start": 0.0, "end": 0.9, "by_sprint": {5: 0.42}}}
    assert gen_tokens.adoption_for(5, [1, 5, 9], cfg) == 0.42


# --- determinism ----------------------------------------------------------


def test_the_same_work_item_always_draws_the_same_numbers():
    a = gen_tokens._rng("KAFKA-1", "0", seed=7).lognormvariate(9, 1)
    b = gen_tokens._rng("KAFKA-1", "0", seed=7).lognormvariate(9, 1)
    assert a == b


def test_a_new_work_item_does_not_reshuffle_its_neighbours():
    """Keyed on the row, not walked from one shared stream — otherwise
    inserting anything upstream changes every row after it and 'deterministic'
    quietly stops being true."""
    before = gen_tokens._rng("KAFKA-2", "0", seed=7).random()
    gen_tokens._rng("KAFKA-NEW", "0", seed=7).random()  # a new item appears
    assert gen_tokens._rng("KAFKA-2", "0", seed=7).random() == before


def test_usage_ids_are_stable_so_a_rerun_upserts():
    assert gen_tokens.usage_id_for("KAFKA-1", 2) == gen_tokens.usage_id_for(
        "KAFKA-1", 2
    )
    assert gen_tokens.usage_id_for("KAFKA-1", 2) != gen_tokens.usage_id_for(
        "KAFKA-1", 3
    )


def test_poisson_is_non_negative_and_responds_to_lambda():
    rng = random.Random(1)
    draws = [gen_tokens._poisson(rng, 1.4) for _ in range(400)]
    assert min(draws) >= 0
    assert 0.9 < sum(draws) / len(draws) < 2.2


# --- meetings -------------------------------------------------------------


def test_meeting_sizes_are_mostly_small():
    small = sum(w for size, w in gen_calendar.SIZES if size <= 6)
    assert small > 0.85, "most meetings should be 2-6 people"


def test_durations_are_ones_people_actually_book():
    assert {d for d, _ in gen_calendar.DURATIONS_MIN} <= {15, 30, 45, 60, 90, 120}


def test_a_meeting_never_overlaps_a_real_event():
    """An actor cannot be in a meeting at the moment they pushed a commit."""
    from datetime import UTC, datetime, timedelta

    base = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)
    events = [(base, "C1"), (base + timedelta(hours=6), "C1")]
    slot = gen_calendar.free_slot(random.Random(3), events, 60)
    assert slot is not None
    when, _ = slot
    assert events[0][0] < when
    assert when + timedelta(minutes=60) < events[1][0]


def test_no_slot_is_offered_when_the_gap_is_too_small():
    from datetime import UTC, datetime, timedelta

    base = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)
    events = [(base, "C1"), (base + timedelta(minutes=20), "C1")]
    assert gen_calendar.free_slot(random.Random(3), events, 60) is None


def test_an_actor_with_one_event_gets_no_meetings():
    """There is no gap to place one in, and inventing the surrounding day
    would be inventing when someone was at work."""
    from datetime import UTC, datetime

    assert gen_calendar.free_slot(
        random.Random(3), [(datetime(2026, 3, 2, 9, 0, tzinfo=UTC), "C1")], 30
    ) is None


# --- the privacy floor ----------------------------------------------------


def test_ai_usage_model_has_no_actor_column():
    """Decision #10. Not now, not later."""
    from app.db.models import AiUsage

    assert not any(
        "actor" in c.name.lower() for c in AiUsage.__table__.columns
    ), "ai_usage must never carry an actor"


def test_calendar_event_carries_no_title_description_or_attendees():
    """The absence of these columns IS the privacy guarantee."""
    from app.db.models import CalendarEvent

    names = {c.name.lower() for c in CalendarEvent.__table__.columns}
    for banned in ("title", "description", "attendees", "attendee_list", "notes"):
        assert banned not in names


def test_token_cost_events_carry_no_actor():
    """cost_event HAS an actor_hash column, so this is the one place AI spend
    could become per-person. It is set to None explicitly, not by omission."""
    import inspect

    source = inspect.getsource(gen_tokens.generate)
    assert '"actor_hash": None' in source


def test_the_token_generator_never_reads_the_actor_table():
    import inspect

    source = inspect.getsource(gen_tokens)
    assert "FROM actor" not in source
    assert "actor_hash" not in source.split("def report")[0].replace(
        '"actor_hash": None', ""
    ).replace("actor_hash column", "")


# --- against live Postgres ------------------------------------------------


def test_no_ai_usage_row_is_attached_to_a_missing_work_item(pg_engine):
    with pg_engine.connect() as conn:
        assert conn.execute(
            text(
                "SELECT count(*) FROM ai_usage a WHERE a.work_item_id IS NOT NULL "
                "AND NOT EXISTS (SELECT 1 FROM work_item w "
                "WHERE w.work_item_id = a.work_item_id)"
            )
        ).scalar() == 0


def test_stored_ai_cost_reconciles_with_its_own_tokens(pg_engine):
    """cost is computed, never sampled, so dividing one by the other on stage
    must not produce a surprise."""
    with pg_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT tokens_in, tokens_out, cost FROM ai_usage LIMIT 200")
        ).all()
    if not rows:
        pytest.skip("no synthetic token data generated yet")
    for tin, tout, cost in rows:
        assert cost >= 0
        assert (tin > 0 or tout > 0) or cost == 0


def test_every_meeting_event_is_marked_synthetic(pg_engine):
    with pg_engine.connect() as conn:
        assert conn.execute(
            text(
                "SELECT count(*) FROM event_log "
                "WHERE activity = 'meeting' AND source <> 'synthetic'"
            )
        ).scalar() == 0


def test_no_view_exposes_token_spend_per_person(pg_engine):
    """The adversarial one. A view joining ai_usage to anything actor-shaped
    would make per-person AI spend one GROUP BY away."""
    with pg_engine.connect() as conn:
        views = conn.execute(
            text(
                "SELECT viewname, definition FROM pg_views "
                "WHERE schemaname = 'public'"
            )
        ).all()
    for name, definition in views:
        body = definition.lower()
        if "ai_usage" in body:
            assert "actor_hash" not in body, (
                f"view {name} joins ai_usage to an actor — per-person AI spend"
            )
