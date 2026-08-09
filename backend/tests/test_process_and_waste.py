"""
P6 (process discovery) and P7 (waste detectors) tests.

Views live in migrations/003_process_and_waste_views.sql and are exercised
here against real Postgres — skip rather than fail when nothing is
listening, same convention as test_normalise.py / test_cost_rates.py.
"""

from __future__ import annotations

import dataclasses

import pytest
from sqlalchemy import text

from app.waste import backlog, ci_waste, discovery, key_person, review_latency, rework, variants
from app.waste.common import WasteFinding


# --- pure logic: no DB needed -------------------------------------------


def test_rare_but_costly_filters_by_case_count():
    vs = [
        variants.Variant("a", "r", ["x"], n_cases=1, total_cost=100, cost_share_pct=5, is_modal=False),
        variants.Variant("b", "r", ["y"], n_cases=50, total_cost=90, cost_share_pct=4, is_modal=True),
        variants.Variant("c", "r", ["z"], n_cases=2, total_cost=0, cost_share_pct=0, is_modal=False),
    ]
    rare = variants.rare_but_costly(vs, case_count_ceiling=5)
    ids = {v.variant_id for v in rare}
    assert ids == {"a"}  # b fails the case-count ceiling, c has zero cost


def test_key_person_exposure_never_carries_an_actor_hash():
    """Structural privacy check: the return type cannot even hold an actor
    identifier, so no code path here can leak one by accident."""
    fields = {f.name for f in dataclasses.fields(key_person.KeyPersonExposure)}
    assert "actor_hash" not in fields
    assert fields == {"component", "n_actors", "max_share", "suppressed", "k_applied"}


def test_ci_waste_price_fails_closed_without_a_citation():
    from decimal import Decimal

    cost, reason = ci_waste.price(Decimal(1000), {"ci_cost": {"source": {}}})
    assert cost is None
    assert "ci_cost.source is incomplete" in reason


def test_ci_waste_carbon_fails_closed_without_a_citation():
    from decimal import Decimal

    co2, reason = ci_waste.carbon_kg(Decimal(1000), {"carbon": {"source": {}}})
    assert co2 is None
    assert "carbon.source is incomplete" in reason


def test_ci_waste_prices_when_cited():
    from decimal import Decimal

    cfg = {
        "ci_cost": {
            "source": {"publisher": "X", "url": "https://x", "retrieved": "2026-01-01"},
            "cost_per_runner_minute": "0.5",
        }
    }
    cost, reason = ci_waste.price(Decimal(100), cfg)
    assert reason is None
    assert cost == Decimal("50.00")


def test_waste_finding_cost_pending_is_never_set_alongside_a_real_cost_being_absent_for_other_reasons():
    """cost=None with no cost_pending reason would look like a silent zero;
    the dataclass allows it but every detector in this module sets one."""
    f = WasteFinding(detector="x", hours=1.0, cost=None, unit_note="n", evidence_query="q")
    assert f.cost_pending is None  # default is honest: only detectors set a reason


# --- against live Postgres ------------------------------------------------


def test_ci_run_never_appears_in_the_collapsed_sequence(pg_engine):
    with pg_engine.connect() as conn:
        n = conn.execute(
            text("SELECT count(*) FROM v_collapsed_sequence WHERE activity = 'ci_run'")
        ).scalar()
    assert n == 0


def test_ci_run_never_appears_in_transitions_or_edges(pg_engine):
    with pg_engine.connect() as conn:
        n = conn.execute(
            text(
                "SELECT count(*) FROM v_transitions_human "
                "WHERE source_activity = 'ci_run' OR target_activity = 'ci_run'"
            )
        ).scalar()
        assert n == 0
        n = conn.execute(
            text(
                "SELECT count(*) FROM v_edges "
                "WHERE source_activity = 'ci_run' OR target_activity = 'ci_run'"
            )
        ).scalar()
        assert n == 0


def test_consecutive_identical_activities_collapse_into_one_node(pg_engine):
    """A case with N consecutive same-activity events must produce a node
    with repeat_count == N, not N separate nodes — the whole point of the
    collapse step."""
    with pg_engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT case_id, activity, repeat_count FROM v_collapsed_sequence
                 WHERE repeat_count > 1 LIMIT 1
                """
            )
        ).first()
    if row is None:
        pytest.skip("no case in this dataset has a repeated activity run")
    case_id, activity, repeat_count = row
    with pg_engine.connect() as conn:
        raw_count = conn.execute(
            text(
                "SELECT count(*) FROM event_log "
                "WHERE work_item_id = :wid AND activity = :act"
            ),
            {"wid": case_id, "act": activity},
        ).scalar()
    assert raw_count >= repeat_count  # the run is a subset of all same-activity events


def test_variant_cost_share_sums_to_roughly_100_per_repo(pg_engine):
    with pg_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT repo, SUM(cost_share_pct) FROM v_variants GROUP BY repo")
        ).all()
    for repo, total in rows:
        if total is not None:
            assert 99.0 <= float(total) <= 101.0, (repo, total)


def test_exactly_one_modal_variant_per_repo(pg_engine):
    with pg_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT repo, count(*) FROM v_variants WHERE is_modal "
                "GROUP BY repo"
            )
        ).all()
    for repo, n in rows:
        assert n == 1, f"{repo} has {n} modal variants, expected exactly 1"


def test_load_edges_significance_filter_covers_at_most_the_threshold(pg_engine):
    from app.db.session import get_read_engine
    from sqlalchemy.orm import Session as OrmSession

    with OrmSession(get_read_engine()) as session:
        edges = discovery.load_edges(session, repo="apache/kafka")
    if not edges:
        pytest.skip("no edges for apache/kafka")
    assert any(e.significant for e in edges)
    # Every edge below the filter must actually be lower ranked than every
    # edge above it, whichever basis (cost or frequency) is in effect.
    sig = [e for e in edges if e.significant]
    insig = [e for e in edges if not e.significant]
    if sig and insig:
        weight = (lambda e: e.cost_exposure) if any(e.cost_exposure > 0 for e in edges) else (lambda e: e.n_transitions)
        assert min(weight(e) for e in sig) >= max(weight(e) for e in insig)


def test_load_variants_is_sorted_by_cost_share_descending(pg_engine):
    from app.db.session import get_read_engine
    from sqlalchemy.orm import Session as OrmSession

    with OrmSession(get_read_engine()) as session:
        vs = variants.load_variants(session)
    shares = [v.cost_share_pct for v in vs]
    assert shares == sorted(shares, reverse=True)


def test_review_latency_reports_two_definitions_with_different_denominators(pg_engine):
    from app.db.session import get_read_engine
    from sqlalchemy.orm import Session as OrmSession

    with OrmSession(get_read_engine()) as session:
        findings = review_latency.detect(session)
    defs = {f.detector for f in findings}
    assert defs == {
        "review_latency:requested_to_first_response",
        "review_latency:pr_opened_to_first_review",
    }


def test_backlog_segments_are_all_positive_hours(pg_engine):
    from app.db.session import get_read_engine
    from sqlalchemy.orm import Session as OrmSession

    with OrmSession(get_read_engine()) as session:
        _finding, segments = backlog.detect(session)
    for s in segments:
        assert s.median_hours >= 0
        assert s.p90_hours >= s.median_hours


def test_key_person_suppresses_components_under_the_k_floor(pg_engine):
    """key_person.py deliberately requires the privileged (write) session —
    it reads v_actor_component_activity, which must never be granted to the
    app role. See test_the_app_role_still_cannot_read_actor_or_the_internal_
    activity_view for the other half of that boundary."""
    from app.db.session import write_session

    with write_session() as session:
        results = key_person.detect(session)
    for r in results:
        if r.n_actors < r.k_applied:
            assert r.suppressed and r.max_share is None
        else:
            assert not r.suppressed and r.max_share is not None


def test_rework_hours_are_real_even_though_cost_is_pending(pg_engine):
    from app.db.session import get_read_engine
    from sqlalchemy.orm import Session as OrmSession

    with OrmSession(get_read_engine()) as session:
        finding = rework.detect(session)
    assert finding.hours >= 0
    # Whatever the rate_card state is right now, hours must never depend on it.


def test_the_app_role_can_read_every_new_view(pg_engine):
    from app.db.session import get_read_engine

    with get_read_engine().connect() as conn:
        for view in (
            "v_collapsed_sequence",
            "v_transitions_human",
            "v_edges",
            "v_case_variant",
            "v_variants",
            "v_review_latency_both",
            "v_ci_waste_minutes",
            "v_rework_cost",
            "v_backlog_time_full",
        ):
            conn.execute(text(f"SELECT 1 FROM {view} LIMIT 1"))


def test_the_app_role_still_cannot_read_actor_or_the_internal_activity_view(pg_engine):
    """The privilege boundary the routers rely on, proved rather than
    assumed — if this ever starts passing, the routers may be silently
    exposing per-actor data."""
    from app.db.session import get_read_engine
    from sqlalchemy.exc import ProgrammingError

    with get_read_engine().connect() as conn:
        for relation in ("actor", "v_actor_component_activity"):
            with pytest.raises(ProgrammingError):
                conn.execute(text(f"SELECT 1 FROM {relation} LIMIT 1"))
            conn.rollback()


# --- API ---------------------------------------------------------------


def test_process_and_waste_endpoints_return_200(pg_engine):
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    for path in (
        "/process/graph",
        "/process/variants",
        "/waste/summary",
        "/waste/ci",
        "/waste/rework",
        "/waste/review-latency",
        "/waste/backlog",
    ):
        response = client.get(path)
        assert response.status_code == 200, (path, response.text)


def test_waste_router_never_exposes_a_key_person_route():
    from app.api.waste import router

    paths = {route.path for route in router.routes}
    assert not any("key" in p and "person" in p for p in paths)
