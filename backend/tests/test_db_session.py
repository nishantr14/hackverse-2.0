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


def _granted_relations(sql: str) -> set[str]:
    granted: set[str] = set()
    for block in sql.split("GRANT SELECT ON")[1:]:
        for token in block.split("TO esi_app", 1)[0].replace("\n", " ").split():
            name = token.strip().strip(",")
            if name:
                granted.add(name)
    return granted


def test_grant_list_matches_the_sql(schema_sql):
    """APP_ROLE_GRANTS is a copy of what the SQL actually grants. Prove it.

    Both sources count. docs/schema.sql is frozen, so a view added later —
    v_event_log and v_case_evidence, in migrations/002 — is granted there
    instead. Reading only the frozen file would make a legitimate grant look
    like drift, which is how a check stops being trusted.
    """
    from pathlib import Path

    migrations = Path(__file__).resolve().parents[1] / "migrations"
    in_sql = _granted_relations(schema_sql)
    for path in sorted(migrations.glob("*.sql")):
        in_sql |= _granted_relations(path.read_text(encoding="utf-8"))

    assert in_sql == set(APP_ROLE_GRANTS), (
        "the SQL grants and APP_ROLE_GRANTS disagree — fix session.py, or the "
        "migration if a view was granted that should not be."
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


# --- test hygiene ---------------------------------------------------------

#: Tables that hold ingested data. Deleting from one of these without scoping
#: the delete destroys work someone waited forty minutes for.
REAL_TABLES = ("raw_payload", "ci_run", "event_log", "work_item", "actor")


def test_no_test_deletes_real_rows_unscoped():
    """DATABASE_URL points at the real database, not a scratch one.

    This exists because two fixtures in test_github_connector.py ran
    `DELETE FROM ci_run` and `DELETE FROM raw_payload WHERE source=...` to
    tidy up after themselves, and every run of the suite silently emptied
    79,085 CI rows and 5,632 fetched PR payloads. A delete in a test must name
    the rows the test itself created.
    """
    import ast
    import re
    from pathlib import Path

    pattern = re.compile(
        r"DELETE\s+FROM\s+(" + "|".join(REAL_TABLES) + r")\b", re.IGNORECASE
    )
    offenders = []
    for path in Path(__file__).parent.glob("test_*.py"):
        # The immutability suite attempts unscoped deletes ON PURPOSE and
        # asserts every one of them is refused. Those statements remove
        # nothing — that is the thing they prove.
        if path.name == "test_raw_payload_immutable.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        # Docstrings talk ABOUT these statements — including this one — so
        # scan executable string literals only.
        docstrings = {
            id(node.body[0].value)
            for node in ast.walk(tree)
            if isinstance(
                node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
            )
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if id(node) in docstrings:
                continue
            match = pattern.search(node.value)
            if match and "= ANY(" not in node.value:
                offenders.append(f"{path.name}:{node.lineno}: {match.group(0)}")
    assert not offenders, (
        "unscoped DELETE against a real table:\n  " + "\n  ".join(offenders)
    )
