"""
DB session management — two engines, two Postgres roles, one direction of trust.
Owner: Nishant (infra lane).
Phase: Tier 0.

    WRITE  (role `esi`)      ingestion + synthetic generators only
    READ   (role `esi_app`)  the API, SELECT on views only

The API is never handed the write engine. That is enforced twice: the read
engine opens its connections with `default_transaction_read_only = on`, and
`get_read_session()` asserts the engine it was given is the read one.

The k-anonymity floor lives in the VIEWS (docs/schema.sql), not in application
code. That is the whole point of the split: the app physically cannot reach
`event_log` or `cost_event` to compute an unsuppressed aggregate, so the floor
is a property of the database rather than a promise about our discipline.
`assert_app_role_grants()` proves that against a live database.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

#: Exactly what docs/schema.sql grants to `esi_app`. Views, plus four base
#: tables that carry no per-actor row (a variant is a path, a rate is public,
#: run_config is our own knobs, backtest_result is model accuracy).
#: This list is a copy of the schema's GRANT statement, not an independent
#: decision. If they disagree, the schema wins and this list is the bug.
APP_ROLE_GRANTS: frozenset[str] = frozenset(
    {
        "v_event_seq",
        "v_transitions",
        "v_case_sequence",
        "v_cycle_time",
        "v_review_latency",
        "v_rework_pairs",
        "v_backlog_time",
        "v_case_cost",
        "v_spend_by_component",
        "variant",
        "backtest_result",
        "rate_card",
        "run_config",
    }
)

#: Relations the app role must NOT be able to read, ever. Each one either
#: carries an actor_hash at row grain or bypasses the k floor.
APP_ROLE_FORBIDDEN: frozenset[str] = frozenset(
    {
        "event_log",
        "cost_event",
        "actor",
        "work_session",
        "ai_usage",
        "calendar_event",
        "code_churn",
        "raw_payload",
        "ingest_cursor",
        "v_actor_component_activity",
    }
)

APP_ROLE = "esi_app"

#: Applied to every read-engine connection. Postgres refuses the write before
#: the grants are even consulted, so a router that somehow issues an INSERT
#: fails at the server rather than relying on us having got the grants right.
READ_ONLY_CONNECT_ARGS: dict[str, str] = {
    "options": "-c default_transaction_read_only=on"
}


class ReadOnlyViolation(RuntimeError):
    """Raised when write access appears somewhere only reads are allowed."""


@lru_cache(maxsize=1)
def get_write_engine() -> Engine:
    """Engine for ingestion and synthetic generation. Never give this to the API."""
    return create_engine(
        get_settings().database_url,
        pool_pre_ping=True,
        future=True,
    )


@lru_cache(maxsize=1)
def get_read_engine() -> Engine:
    """Engine for the API. Connections are read-only at the server, not by convention."""
    return create_engine(
        get_settings().database_url_readonly,
        pool_pre_ping=True,
        future=True,
        connect_args=READ_ONLY_CONNECT_ARGS,
    )


WriteSessionLocal = sessionmaker(bind=None, class_=Session, expire_on_commit=False)
ReadSessionLocal = sessionmaker(bind=None, class_=Session, expire_on_commit=False)


@contextmanager
def write_session() -> Iterator[Session]:
    """Transactional scope for ingestion. Commits on success, rolls back on error."""
    session = WriteSessionLocal(bind=get_write_engine())
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_read_session() -> Iterator[Session]:
    """FastAPI dependency. Yields a session that cannot write.

    Routers depend on this and nothing else. There is no dependency in this
    module that hands the API a writable session, and there must never be one.
    """
    engine = get_read_engine()
    assert_engine_is_read_only(engine)
    session = ReadSessionLocal(bind=engine)
    try:
        yield session
    finally:
        session.close()


def assert_engine_is_read_only(engine: Engine) -> None:
    """Fail loudly if the API was handed the write engine.

    Compares the connection URL rather than trusting the caller, so wiring the
    wrong engine into a router is caught at request time, not in review.
    """
    write_url = get_write_engine().url
    if engine.url == write_url:
        raise ReadOnlyViolation(
            "the API was handed the WRITE engine; it must use "
            "DATABASE_URL_READONLY (role esi_app)"
        )
    if engine.url.username == write_url.username:
        raise ReadOnlyViolation(
            f"read engine is connecting as the write role {engine.url.username!r}; "
            f"DATABASE_URL_READONLY must use the {APP_ROLE!r} role"
        )


def assert_app_role_grants(engine: Engine | None = None) -> None:
    """Verify the live database matches the privacy contract.

    Two assertions against a real Postgres:

    1. `esi_app` can SELECT everything in APP_ROLE_GRANTS — otherwise the API
       is broken and views silently return permission errors.
    2. `esi_app` can SELECT nothing in APP_ROLE_FORBIDDEN — otherwise the
       k-anonymity floor is bypassable and the privacy claim is false.

    This VERIFIES; it does not grant. The grants belong in docs/schema.sql,
    which is frozen. Application code that widens its own permissions is
    exactly the hole this design exists to close.
    """
    engine = engine or get_write_engine()
    missing: list[str] = []
    leaked: list[str] = []

    with engine.connect() as conn:
        for relation in sorted(APP_ROLE_GRANTS):
            granted = conn.execute(
                text("SELECT has_table_privilege(:role, :rel, 'SELECT')"),
                {"role": APP_ROLE, "rel": relation},
            ).scalar_one()
            if not granted:
                missing.append(relation)

        for relation in sorted(APP_ROLE_FORBIDDEN):
            granted = conn.execute(
                text("SELECT has_table_privilege(:role, :rel, 'SELECT')"),
                {"role": APP_ROLE, "rel": relation},
            ).scalar_one()
            if granted:
                leaked.append(relation)

    problems: list[str] = []
    if leaked:
        problems.append(
            f"PRIVACY VIOLATION: role {APP_ROLE!r} can read {leaked} — "
            "these bypass the k-anonymity floor"
        )
    if missing:
        problems.append(
            f"role {APP_ROLE!r} is missing SELECT on {missing} — the API will fail"
        )
    if problems:
        raise ReadOnlyViolation("; ".join(problems))
