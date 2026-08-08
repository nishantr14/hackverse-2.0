"""
docs/schema.sql is frozen and is the contract. These tests fail when
backend/app/db/models.py drifts from it.

The direction of blame is fixed: if one of these fails, the MODEL is wrong.
Changing the SQL to make a test pass requires all four teammates present, and
would silently invalidate every query the other three have written against it.
"""

from __future__ import annotations

from sqlalchemy import ARRAY, Boolean, DateTime, Integer, Numeric, Text
from sqlalchemy.dialects.postgresql import JSONB

from app.db.models import Base
from tests.conftest import SqlColumn

_SA_TO_CANONICAL = [
    (JSONB, lambda t: "JSONB"),
    (ARRAY, lambda t: f"{t.item_type.__class__.__name__.upper()}[]"),
    (DateTime, lambda t: "TIMESTAMPTZ" if t.timezone else "TIMESTAMP"),
    (Boolean, lambda t: "BOOLEAN"),
    (Integer, lambda t: "INTEGER"),
    (Numeric, lambda t: "NUMERIC"),
    (Text, lambda t: "TEXT"),
]


def _canonical(sa_type: object) -> str:
    for cls, render in _SA_TO_CANONICAL:
        if isinstance(sa_type, cls):
            return render(sa_type)
    raise AssertionError(f"unmapped SQLAlchemy type {sa_type!r}")


def _model_tables() -> dict[str, list[SqlColumn]]:
    return {
        name: [
            SqlColumn(
                name=col.name, type=_canonical(col.type), nullable=bool(col.nullable)
            )
            for col in table.columns
        ]
        for name, table in Base.metadata.tables.items()
    }


def test_every_sql_table_has_a_model(schema_tables):
    missing = sorted(set(schema_tables) - set(_model_tables()))
    assert not missing, f"docs/schema.sql defines tables with no model: {missing}"


def test_no_model_invents_a_table(schema_tables):
    extra = sorted(set(_model_tables()) - set(schema_tables))
    assert not extra, (
        f"models.py defines tables absent from docs/schema.sql: {extra}. "
        "The schema is frozen; the model does not get to add tables."
    )


def test_columns_match_name_type_and_nullability(schema_tables):
    models = _model_tables()
    drift: list[str] = []
    for table, sql_cols in schema_tables.items():
        model_cols = models.get(table)
        if model_cols is None:
            continue
        sql_by_name = {c.name: c for c in sql_cols}
        model_by_name = {c.name: c for c in model_cols}

        for name in sorted(set(sql_by_name) - set(model_by_name)):
            drift.append(f"{table}.{name}: in SQL, missing from model")
        for name in sorted(set(model_by_name) - set(sql_by_name)):
            drift.append(f"{table}.{name}: in model, absent from SQL")
        for name in sorted(set(sql_by_name) & set(model_by_name)):
            sql_col, model_col = sql_by_name[name], model_by_name[name]
            if sql_col.type != model_col.type:
                drift.append(
                    f"{table}.{name}: SQL {sql_col.type} vs model {model_col.type}"
                )
            if sql_col.nullable != model_col.nullable:
                drift.append(
                    f"{table}.{name}: SQL nullable={sql_col.nullable} vs "
                    f"model nullable={model_col.nullable}"
                )
    assert not drift, "model/schema drift:\n  " + "\n  ".join(drift)


def test_column_order_matches(schema_tables):
    """Order is cosmetic to Postgres but keeps side-by-side review honest."""
    models = _model_tables()
    for table, sql_cols in schema_tables.items():
        if table not in models:
            continue
        assert [c.name for c in sql_cols] == [c.name for c in models[table]], (
            f"{table}: column order differs from docs/schema.sql"
        )


def test_event_log_actor_hash_is_nullable(schema_tables):
    """A CI run has no human behind it. This was the point of the amendment."""
    col = {c.name: c for c in schema_tables["event_log"]}["actor_hash"]
    assert col.nullable
    assert Base.metadata.tables["event_log"].c.actor_hash.nullable


def test_ai_usage_has_no_actor_column(schema_tables):
    """Locked decision #10. Per-person AI usage must not be queryable at all."""
    names = {c.name for c in schema_tables["ai_usage"]}
    assert "actor_hash" not in names
    assert "actor_hash" not in Base.metadata.tables["ai_usage"].c
