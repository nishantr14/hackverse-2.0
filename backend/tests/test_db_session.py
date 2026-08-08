"""
Read/write split tests.

The claim under test: the API cannot reach a base table, so the k-anonymity
floor in the views cannot be bypassed by application code — not by accident,
and not by a teammate under time pressure at hour 26.
"""

from __future__ import annotations

import inspect

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db.session import (
    APP_ROLE_FORBIDDEN,
    APP_ROLE_GRANTS,
    READ_ONLY_CONNECT_ARGS,
    ReadOnlyViolation,
    assert_app_role_grants,
    assert_engine_is_read_only,
    get_read_engine,
    get_write_engine,
)


def test_read_and_write_urls_are_different_roles():
    read_url, write_url = get_read_engine().url, get_write_engine().url
    assert read_url.username != write_url.username, (
        "DATABASE_URL and DATABASE_URL_READONLY use the same role; the split "
        "is decorative"
    )


def test_handing_the_api_the_write_engine_raises():
    with pytest.raises(ReadOnlyViolation):
        assert_engine_is_read_only(get_write_engine())


def test_read_engine_passes_the_guard():
    assert_engine_is_read_only(get_read_engine())


def test_read_engine_opens_read_only_transactions():
    """SQLAlchemy hides merged connect_args, so assert on the constant the
    engine is built from — and see test_app_role_cannot_write below for the
    behavioural proof against real Postgres."""
    assert "default_transaction_read_only=on" in READ_ONLY_CONNECT_ARGS["options"]
    assert inspect.getsource(get_read_engine).count("READ_ONLY_CONNECT_ARGS") == 1


def test_forbidden_set_covers_every_per_actor_relation():
    """These are the relations that carry an actor_hash at row grain, plus the
    internal per-actor view. Each is a re-identification surface."""
    for relation in (
        "event_log",
        "cost_event",
        "actor",
        "work_session",
        "v_actor_component_activity",
    ):
        assert relation in APP_ROLE_FORBIDDEN


def test_grants_and_forbidden_sets_do_not_overlap():
    assert not APP_ROLE_GRANTS & APP_ROLE_FORBIDDEN


def test_grant_list_matches_the_frozen_schema(schema_sql):
    """APP_ROLE_GRANTS is a copy of the schema's GRANT statement. Prove it."""
    grant_block = schema_sql.split("GRANT SELECT ON", 1)[1].split("TO esi_app", 1)[0]
    in_sql = {
        token.strip().strip(",")
        for token in grant_block.replace("\n", " ").split()
        if token.strip().strip(",")
    }
    assert in_sql == set(APP_ROLE_GRANTS), (
        "docs/schema.sql and APP_ROLE_GRANTS disagree. The schema is frozen — "
        "fix session.py."
    )


# --- live database -------------------------------------------------------


def test_app_role_grants_match_the_contract(pg_engine):
    assert_app_role_grants(pg_engine)


def test_app_role_cannot_read_the_event_log(pg_engine):
    """The direct form of the claim, run against real Postgres."""
    with get_read_engine().connect() as conn, pytest.raises(DBAPIError) as excinfo:
        conn.execute(text("SELECT count(*) FROM event_log"))
    assert "permission denied" in str(excinfo.value).lower()


def test_app_role_cannot_read_the_per_actor_view(pg_engine):
    """v_actor_component_activity is per-actor by construction. Never exposed."""
    with get_read_engine().connect() as conn, pytest.raises(DBAPIError) as excinfo:
        conn.execute(text("SELECT * FROM v_actor_component_activity LIMIT 1"))
    assert "permission denied" in str(excinfo.value).lower()


def test_app_role_can_read_the_k_floored_view(pg_engine):
    """The other half: suppression works only if the view is actually readable."""
    with get_read_engine().connect() as conn:
        conn.execute(text("SELECT * FROM v_spend_by_component LIMIT 1"))


def test_app_role_cannot_write(pg_engine):
    with get_read_engine().connect() as conn, pytest.raises(DBAPIError) as excinfo:
        conn.execute(text("INSERT INTO run_config (key, value) VALUES ('x', 'y')"))
    message = str(excinfo.value).lower()
    assert "read-only" in message or "permission denied" in message
