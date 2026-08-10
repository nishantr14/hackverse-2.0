"""
End-to-end tests for the wired demo: cost attribution, the simulator, and
the four routers the frontend actually calls.

These exist because the frontend consumes these shapes literally. A renamed
key here is a blank screen there, and a blank screen at hour 30 is not
debuggable in the time available.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.cost.cost_attribution import attribute, load_config
from app.db.session import get_read_session, write_session
from app.main import app
from app.models.simulator import Scenario, ScenarioRefused, list_components, run


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(scope="module")
def components(pg_engine):
    with next(get_read_session()) as session:
        return list_components(session, limit=6)


# --- the routers are mounted at all --------------------------------------


def test_every_router_the_frontend_needs_is_mounted():
    from app.main import MOUNTED_ROUTERS

    assert set(MOUNTED_ROUTERS) == {
        "spend",
        "waste",
        "process",
        "simulate",
        # The named layer. Mounted from a separate store that cannot see the
        # warehouse — see test_workforce.py for the guards on that boundary.
        "workforce",
    }


# --- cost attribution ----------------------------------------------------


def test_cost_attribution_is_idempotent(pg_engine):
    """Re-running must upsert, not double the spend.

    cost_event_id is derived from the source row's own primary key precisely
    so this holds. If it ever stops holding, every rupee on every screen
    inflates on the next re-run and nothing else says so.
    """
    def total() -> float:
        with pg_engine.connect() as conn:
            return float(
                conn.execute(text("SELECT COALESCE(SUM(cost),0) FROM cost_event")).scalar()
            )

    before = total()
    with write_session() as session:
        attribute(session, load_config())
        session.commit()
    assert total() == pytest.approx(before, rel=1e-9)


def test_ci_rows_carry_cost_but_never_engineer_hours(pg_engine):
    """Runner minutes are machine time. Summing them into `hours` alongside
    session hours produces a total that means nothing while looking
    authoritative."""
    with pg_engine.connect() as conn:
        leaked = conn.execute(
            text("SELECT count(*) FROM cost_event WHERE basis='ci_runner' AND hours IS NOT NULL")
        ).scalar()
    assert leaked == 0


def test_every_cost_event_has_a_known_basis(pg_engine):
    with pg_engine.connect() as conn:
        bases = {r[0] for r in conn.execute(text("SELECT DISTINCT basis FROM cost_event"))}
    assert bases <= {"session_inferred", "ci_runner", "ai_tokens", "meeting"}
    assert "session_inferred" in bases, "no labour cost landed at all"


# --- the simulator -------------------------------------------------------


def test_simulator_refuses_a_move_to_the_same_component(components):
    a = components[0]
    with pytest.raises(ScenarioRefused, match="same component"):
        run(Scenario(source=a, destination=a, engineer_count=3))


def test_simulator_refuses_moving_more_engineers_than_exist(components):
    a, b = components[0], components[1]
    with pytest.raises(ScenarioRefused, match="cannot move"):
        run(Scenario(source=a, destination=b, engineer_count=a.n_engineers + 1))


def test_simulator_is_deterministic(components):
    """Same scenario, same bytes. Twice on stage must agree."""
    scenario = Scenario(source=components[0], destination=components[1], engineer_count=4)
    assert run(scenario) == run(scenario)


def test_moving_people_off_a_component_slips_it_and_helps_the_other(components):
    """The sign convention the whole screen depends on: source positive
    (later), destination negative (earlier)."""
    result = run(
        Scenario(source=components[0], destination=components[1], engineer_count=5)
    )
    assert result.source_delta_weeks > 0, "losing engineers must not speed a project up"
    assert result.dest_delta_weeks < 0, "gaining engineers must not slow a project down"


def test_ramp_up_means_the_destination_gains_less_than_it_was_given(components):
    result = run(
        Scenario(source=components[0], destination=components[1], engineer_count=10)
    )
    assert result.ramp_up_penalty_applied
    assert "effective engineers" in (result.ramp_up_note or "")


def test_a_bigger_move_never_helps_the_destination_less(components):
    small = run(Scenario(source=components[0], destination=components[1], engineer_count=2))
    large = run(Scenario(source=components[0], destination=components[1], engineer_count=8))
    assert large.dest_delta_weeks <= small.dest_delta_weeks
    assert large.source_delta_weeks >= small.source_delta_weeks


# --- the shapes the frontend consumes ------------------------------------

SPEND_ROW_KEYS = {
    "workItem",
    "project",
    "component",
    "authorHours",
    "reviewHours",
    "cost",
    "labourCost",
}


def test_spend_rows_match_the_frontend_contract(client, pg_engine):
    rows = client.get("/spend?limit=5").json()
    assert rows, "/spend returned nothing — the spend screen would be blank"
    for row in rows:
        assert set(row) == SPEND_ROW_KEYS
        assert row["cost"] > 0
        # Labour is a SLICE of cost, never the whole of it and never more.
        # A blended rate divides labourCost by the hours; if labour ever
        # exceeded total, that rate would exceed the staff band silently.
        assert 0 <= row["labourCost"] <= row["cost"]


def test_review_latency_rows_are_medians_not_sums(client, pg_engine):
    """Waiting runs in parallel. Summing it produced 182,505 days — five
    centuries — which is worse than useless on a slide."""
    rows = [r for r in client.get("/waste/by-project").json()["rows"] if r["type"] == "latency"]
    worst = max(r["hours"] for r in rows if r["nItems"] >= 5)
    assert worst / 24 < 400, f"median wait of {worst / 24:.0f} days is not a median"


def test_spend_summary_carries_a_citation(client, pg_engine):
    """The source string renders on screen beside the money."""
    body = client.get("/spend/summary").json()
    assert body["totalCost"] > 0
    assert body["citation"]["labour"] and "http" in body["citation"]["labour"]
    assert body["citation"]["error"] is None
    assert set(body["byBasis"]) == {"labour", "ci", "ai", "meeting"}


def test_process_map_shares_sum_to_one(client, pg_engine):
    """Variant classes are mutually exclusive by construction. If the shares
    stop summing to 1 the classification has started double-counting."""
    body = client.get("/process/map").json()
    assert body["nodes"] and body["edges"]
    work = sum(v["shareOfWorkItems"] for v in body["variantSummary"])
    cost = sum(v["shareOfCost"] for v in body["variantSummary"])
    assert work == pytest.approx(1.0, abs=1e-6)
    assert cost == pytest.approx(1.0, abs=1e-6)


def test_process_map_edges_carry_a_variant_the_ui_can_colour(client, pg_engine):
    edges = client.get("/process/map").json()["edges"]
    assert {e["variant"] for e in edges} <= {"happy_path", "rework_loop", "triple_review"}
    for edge in edges:
        assert set(edge) == {"from", "to", "variant", "frequency", "costRupees"}


def test_review_latency_is_reported_but_never_priced(client, pg_engine):
    """Waiting is wall clock, not paid engineer time. This is the decision
    that keeps the headline waste figure defensible."""
    body = client.get("/waste/by-project").json()
    latency = [r for r in body["rows"] if r["type"] == "latency"]
    assert latency, "no latency rows at all"
    assert all(r["amountRupees"] is None for r in latency)
    assert all(r["hours"] > 0 for r in latency)


def test_simulate_endpoint_answers_and_refuses_in_the_right_shapes(client, components):
    a, b = components[0], components[1]
    ok = client.post(
        "/simulate",
        json={"sourceProject": a.key, "destProject": b.key, "engineerCount": 5},
    )
    assert ok.status_code == 200
    body = ok.json()
    for key in (
        "sourceDeltaWeeks",
        "destDeltaWeeks",
        "netCostRupees",
        "confidenceLow",
        "confidenceHigh",
        "rampUpPenaltyApplied",
    ):
        assert key in body
    assert body["confidenceHigh"] > body["confidenceLow"]

    refused = client.post(
        "/simulate",
        json={"sourceProject": a.key, "destProject": a.key, "engineerCount": 5},
    )
    assert refused.status_code == 422
    assert "same component" in refused.json()["detail"]


def test_simulate_components_only_offers_forecastable_ones(client, pg_engine):
    body = client.get("/simulate/components?limit=10").json()
    assert len(body["projects"]) >= 2
    for detail in body["detail"]:
        assert detail["engineers"] > 0
        assert detail["itemsPerWeek"] > 0


# --- migrations actually reached this database ---------------------------


def test_every_migration_is_idempotent_and_applies(pg_engine):
    """The runner is the only thing standing between a fresh clone and the
    failure this whole file exists because of: seven routes returning 500
    against views that were never created, on a database that looked fine.

    Running it twice in a row also proves idempotency, which is what lets it
    have no applied-migrations ledger.
    """
    from app.db.migrate import apply_all, migration_files

    assert migration_files(), "no migrations on disk"
    assert apply_all() > 0
    assert apply_all() > 0  # second pass must be a no-op, not an error


def test_every_granted_relation_actually_exists(pg_engine):
    """APP_ROLE_GRANTS names what the API is allowed to read. A name in that
    set with no relation behind it is a route that 500s in production and
    passes every unit test."""
    from app.db.session import APP_ROLE_GRANTS

    with pg_engine.connect() as conn:
        present = {
            r[0]
            for r in conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public'"
                )
            )
        }
    missing = sorted(set(APP_ROLE_GRANTS) - present)
    assert not missing, f"granted but never created: {missing}"


# --- privacy regression --------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/spend?limit=50",
        "/spend/summary",
        "/spend/by-component",
        "/process/map",
        "/process/graph",
        "/waste/by-project",
        "/waste/summary",
        "/simulate/components",
    ],
)
def test_no_endpoint_serves_an_actor_hash(client, pg_engine, path):
    """Every route the browser can reach, checked for per-person identifiers.

    actor_hash is pseudonymous, not anonymous — a hash that appears on a
    per-row payload can still be correlated across screens into one person's
    activity. No route needs one, so none may emit one.
    """
    body = client.get(path).text
    assert "actor_hash" not in body
    assert "actorHash" not in body
