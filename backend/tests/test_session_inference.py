"""
Session inference: gap clustering, lead-in, the daily cap, and the
per-work-item split. Pure-function tests — no live Postgres needed for the
clustering logic itself, only for the two queries at the bottom.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.cost.session_inference import (
    EVENTS_SQL,
    RawEvent,
    apply_daily_cap,
    cluster_events,
    session_id_for,
    split_by_work_item,
)

ACTOR_A = "a" * 16
ACTOR_B = "b" * 16
T0 = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)


def ev(actor=ACTOR_A, item="w1", event="e1", minutes_after_t0=0) -> RawEvent:
    return RawEvent(
        actor_hash=actor,
        work_item_id=item,
        event_id=event,
        ts=T0 + timedelta(minutes=minutes_after_t0),
    )


# --- clustering -------------------------------------------------------


def test_events_within_the_gap_form_one_cluster():
    events = [ev(event="e1", minutes_after_t0=0), ev(event="e2", minutes_after_t0=30)]
    clusters = cluster_events(events, gap_minutes=90, lead_in_minutes=0)
    assert len(clusters) == 1
    assert clusters[0].events == tuple(events)


def test_events_beyond_the_gap_split_into_separate_clusters():
    events = [ev(event="e1", minutes_after_t0=0), ev(event="e2", minutes_after_t0=200)]
    clusters = cluster_events(events, gap_minutes=90, lead_in_minutes=0)
    assert len(clusters) == 2


def test_gap_boundary_is_exclusive_not_inclusive():
    """Exactly the gap threshold apart still joins — only *over* splits."""
    events = [ev(event="e1", minutes_after_t0=0), ev(event="e2", minutes_after_t0=90)]
    clusters = cluster_events(events, gap_minutes=90, lead_in_minutes=0)
    assert len(clusters) == 1


def test_lead_in_extends_the_start_not_the_end():
    events = [ev(event="e1", minutes_after_t0=0), ev(event="e2", minutes_after_t0=20)]
    clusters = cluster_events(events, gap_minutes=90, lead_in_minutes=30)
    (c,) = clusters
    assert c.started_at == T0 - timedelta(minutes=30)
    assert c.ended_at == T0 + timedelta(minutes=20)


def test_different_actors_never_share_a_cluster_even_with_no_gap():
    events = [
        ev(actor=ACTOR_A, event="e1", minutes_after_t0=0),
        ev(actor=ACTOR_B, event="e2", minutes_after_t0=1),
    ]
    clusters = cluster_events(events, gap_minutes=90, lead_in_minutes=0)
    assert len(clusters) == 2
    assert {c.actor_hash for c in clusters} == {ACTOR_A, ACTOR_B}


def test_raw_hours_is_the_span_from_lead_in_to_last_event():
    events = [ev(event="e1", minutes_after_t0=0), ev(event="e2", minutes_after_t0=60)]
    (c,) = cluster_events(events, gap_minutes=90, lead_in_minutes=30)
    assert c.raw_hours == pytest.approx(1.5)  # 30m lead-in + 60m span


def test_no_events_produces_no_clusters():
    assert cluster_events([], gap_minutes=90, lead_in_minutes=30) == []


# --- daily cap ----------------------------------------------------------


def test_a_day_under_the_cap_is_left_alone():
    events = [ev(event="e1", minutes_after_t0=0), ev(event="e2", minutes_after_t0=200)]
    clusters = cluster_events(events, gap_minutes=90, lead_in_minutes=0)
    capped = apply_daily_cap(clusters, cap_hours=10)
    assert capped == [c.raw_hours for c in clusters]


def _chain(start_min, count, step_min, actor=ACTOR_A, item="w1"):
    """A run of events step_min apart (< the gap, so they stay one cluster),
    spanning (count-1)*step_min minutes end to end."""
    return [
        ev(actor=actor, item=item, event=f"e{start_min}-{i}", minutes_after_t0=start_min + i * step_min)
        for i in range(count)
    ]


def test_a_day_over_the_cap_is_scaled_proportionally_not_truncated():
    """Two four-hour sessions the same day, cap at 6h: both shrink to 3h each,
    not one full session plus one zeroed one."""
    events_a = _chain(start_min=0, count=4, step_min=80)  # spans 0-240min = 4h
    events_b = _chain(start_min=400, count=4, step_min=80)  # spans 400-640min = 4h
    clusters = cluster_events(events_a + events_b, gap_minutes=90, lead_in_minutes=0)
    assert len(clusters) == 2  # sanity: the gap really did split them
    assert clusters[0].raw_hours == pytest.approx(4.0)
    assert clusters[1].raw_hours == pytest.approx(4.0)
    capped = apply_daily_cap(clusters, cap_hours=6.0)
    assert sum(capped) == pytest.approx(6.0)
    assert capped[0] == pytest.approx(capped[1])


def test_cap_only_applies_within_the_same_actor_day():
    """Two different actors each do 4h of continuous work the same day. Cap
    is 6h: neither is scaled, even though the two together exceed it — the
    cap is per actor, not a shared daily budget."""
    events_a = _chain(start_min=0, count=4, step_min=80, actor=ACTOR_A)
    events_b = _chain(start_min=0, count=4, step_min=80, actor=ACTOR_B)
    events = sorted(events_a + events_b, key=lambda e: (e.actor_hash, e.ts))
    clusters = cluster_events(events, gap_minutes=90, lead_in_minutes=0)
    assert len(clusters) == 2
    capped = apply_daily_cap(clusters, cap_hours=6.0)
    assert capped == [c.raw_hours for c in clusters]
    assert sorted(capped) == pytest.approx([4.0, 4.0])


# --- per-work-item split -------------------------------------------------


def test_hours_split_proportional_to_event_count_per_item():
    events = [
        ev(item="w1", event="e1", minutes_after_t0=0),
        ev(item="w1", event="e2", minutes_after_t0=10),
        ev(item="w1", event="e3", minutes_after_t0=20),
        ev(item="w2", event="e4", minutes_after_t0=30),
    ]
    clusters = cluster_events(events, gap_minutes=90, lead_in_minutes=0)
    capped = apply_daily_cap(clusters, cap_hours=10)
    rows = split_by_work_item(clusters, capped)

    by_item = {r["work_item_id"]: r for r in rows}
    assert by_item["w1"]["n_events"] == 3
    assert by_item["w2"]["n_events"] == 1
    total_hours = clusters[0].raw_hours
    assert by_item["w1"]["hours"] == pytest.approx(total_hours * 0.75, abs=1e-3)
    assert by_item["w2"]["hours"] == pytest.approx(total_hours * 0.25, abs=1e-3)


def test_split_rows_sum_back_to_the_capped_cluster_hours():
    events = [
        ev(item="w1", event="e1", minutes_after_t0=0),
        ev(item="w2", event="e2", minutes_after_t0=10),
        ev(item="w1", event="e3", minutes_after_t0=20),
    ]
    clusters = cluster_events(events, gap_minutes=90, lead_in_minutes=0)
    capped = apply_daily_cap(clusters, cap_hours=10)
    rows = split_by_work_item(clusters, capped)
    assert sum(r["hours"] for r in rows) == pytest.approx(capped[0], abs=1e-3)


def test_single_item_cluster_produces_one_row_with_all_the_hours():
    events = [ev(item="w1", event="e1", minutes_after_t0=0)]
    clusters = cluster_events(events, gap_minutes=90, lead_in_minutes=30)
    capped = apply_daily_cap(clusters, cap_hours=10)
    rows = split_by_work_item(clusters, capped)
    assert len(rows) == 1
    assert rows[0]["hours"] == pytest.approx(0.5)  # lead-in only


# --- session ids -----------------------------------------------------


def test_session_id_is_deterministic():
    a = session_id_for(ACTOR_A, T0, "w1")
    b = session_id_for(ACTOR_A, T0, "w1")
    assert a == b


def test_session_id_differs_by_work_item_within_the_same_cluster():
    a = session_id_for(ACTOR_A, T0, "w1")
    b = session_id_for(ACTOR_A, T0, "w2")
    assert a != b


def test_session_id_differs_by_actor():
    a = session_id_for(ACTOR_A, T0, "w1")
    b = session_id_for(ACTOR_B, T0, "w1")
    assert a != b


# --- against live Postgres ------------------------------------------------


def test_events_sql_excludes_ci_run(pg_engine):
    """ci_run is a machine event with no human attached — clustering it in
    would stretch a session across automated activity nobody performed."""
    with pg_engine.connect() as conn:
        returned_ids = {r[2] for r in conn.execute(text(EVENTS_SQL)).all()}
        ci_ids = {
            r[0]
            for r in conn.execute(
                text("SELECT event_id FROM event_log WHERE activity = 'ci_run'")
            ).all()
        }
    assert returned_ids, "no events returned — run ingestion first"
    assert not (returned_ids & ci_ids)


def test_events_sql_never_returns_a_null_actor(pg_engine):
    with pg_engine.connect() as conn:
        rows = conn.execute(text(EVENTS_SQL + " LIMIT 5000")).all()
    assert all(r[0] is not None for r in rows), "actor_hash must never be NULL here"


def test_events_sql_returns_rows_sorted_by_actor_then_time(pg_engine):
    with pg_engine.connect() as conn:
        rows = conn.execute(text(EVENTS_SQL + " LIMIT 5000")).all()
    keys = [(r[0], r[3]) for r in rows]
    assert keys == sorted(keys), "load_events()/cluster_events() assume this ordering"
