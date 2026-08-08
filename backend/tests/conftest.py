"""
Shared fixtures.
Owner: Nishant (infra lane).

Design note: most of these tests must pass with NO database running. A
teammate who has just cloned the repo on a laptop without Docker still needs
`pytest` to tell them whether the models have drifted from docs/schema.sql —
that check is a static comparison against the SQL text, not a query.

The handful of tests that genuinely need Postgres take the `pg_engine`
fixture, which SKIPS (never fails) when nothing is listening. Skips are
reported, so a missing database is visible rather than silently green.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "docs" / "schema.sql"

_SQL_TO_CANONICAL = {
    "TEXT": "TEXT",
    "TEXT[]": "TEXT[]",
    "TIMESTAMPTZ": "TIMESTAMPTZ",
    "NUMERIC": "NUMERIC",
    "INTEGER": "INTEGER",
    "INT": "INTEGER",
    "BOOLEAN": "BOOLEAN",
    "JSONB": "JSONB",
}

_NON_COLUMN_PREFIXES = (
    "check",
    "primary key",
    "unique",
    "foreign key",
    "constraint",
    "--",
)


@dataclass(frozen=True)
class SqlColumn:
    name: str
    type: str
    nullable: bool


def parse_schema_tables(sql_text: str) -> dict[str, list[SqlColumn]]:
    """Extract CREATE TABLE definitions from schema SQL.

    Intentionally a small hand-rolled parser rather than a dependency: it only
    has to understand the subset of DDL this one frozen file uses, and adding a
    SQL-parsing library to satisfy a test would be the tail wagging the dog.
    """
    tables: dict[str, list[SqlColumn]] = {}
    current: str | None = None
    for raw_line in sql_text.splitlines():
        line = raw_line.strip()
        if current is None:
            match = re.match(r"CREATE TABLE (\w+)\s*\(", line, re.IGNORECASE)
            if match:
                current = match.group(1)
                tables[current] = []
            continue
        if line.startswith(")"):
            current = None
            continue
        line = re.sub(r"--.*$", "", line).strip().rstrip(",").strip()
        if not line or line.lower().startswith(_NON_COLUMN_PREFIXES):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        name, sql_type = parts[0], parts[1].upper()
        canonical = _SQL_TO_CANONICAL.get(sql_type)
        if canonical is None:
            raise AssertionError(
                f"{current}.{name}: unrecognised SQL type {sql_type!r}. "
                "Teach conftest._SQL_TO_CANONICAL about it."
            )
        upper = line.upper()
        nullable = "NOT NULL" not in upper and "PRIMARY KEY" not in upper
        tables[current].append(SqlColumn(name=name, type=canonical, nullable=nullable))
    return tables


@pytest.fixture(scope="session")
def schema_sql() -> str:
    assert SCHEMA_PATH.exists(), f"{SCHEMA_PATH} is missing — it is the contract"
    return SCHEMA_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def schema_tables(schema_sql: str) -> dict[str, list[SqlColumn]]:
    return parse_schema_tables(schema_sql)


@pytest.fixture(scope="session")
def pg_engine() -> Engine:
    """A live Postgres engine, or a skip. Never a failure."""
    from app.db.session import get_write_engine

    engine = get_write_engine()
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        pytest.skip(
            f"no Postgres at DATABASE_URL ({type(exc).__name__}) — start it "
            "with: docker compose -f infra/docker-compose.yml up -d postgres"
        )
    return engine


@pytest.fixture
def tmp_identity_db(tmp_path: Path) -> Path:
    return tmp_path / "identity.db"


@pytest.fixture
def scratch_engine(tmp_path: Path) -> Engine:
    """A throwaway SQLite engine for tests that only need *an* engine object."""
    return create_engine(f"sqlite:///{tmp_path / 'scratch.db'}")
