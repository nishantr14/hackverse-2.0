"""
SQLAlchemy 2.0 models — a column-for-column mirror of docs/schema.sql.
Owner: Nishant (infra lane).
Phase: Tier 0.

docs/schema.sql is the frozen contract and is applied to Postgres directly.
These models exist so Python code has typed access to those tables; they are
NEVER the source of truth. If a model and the SQL disagree, the model is wrong.
`/verify` check 2 and tests/test_models_match_schema.py both enforce that.

Nothing here carries identity. There is no login, email, name or salary column
in this file, and adding one is a privacy incident, not a feature.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

#: TIMESTAMPTZ. Every timestamp in this system is stored UTC and converted
#: only at render time.
TZDateTime = DateTime(timezone=True)


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------
# 0. CONFIG AND PROVENANCE
# ---------------------------------------------------------------------


class RunConfig(Base):
    """Every knob that moves a number on screen, recorded with a note."""

    __tablename__ = "run_config"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)


# ---------------------------------------------------------------------
# 1. RAW LANDING
# ---------------------------------------------------------------------


class RawPayload(Base):
    """Land every API response here before anything parses it."""

    __tablename__ = "raw_payload"

    source: Mapped[str] = mapped_column(Text, primary_key=True)
    entity_type: Mapped[str] = mapped_column(Text, primary_key=True)
    entity_id: Mapped[str] = mapped_column(Text, primary_key=True)
    fetched_at: Mapped[dt.datetime] = mapped_column(
        TZDateTime, nullable=False, server_default=func.now()
    )
    body: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class IngestCursor(Base):
    __tablename__ = "ingest_cursor"

    source: Mapped[str] = mapped_column(Text, primary_key=True)
    scope: Mapped[str] = mapped_column(Text, primary_key=True)
    cursor: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[dt.datetime] = mapped_column(
        TZDateTime, nullable=False, server_default=func.now()
    )


# ---------------------------------------------------------------------
# 2. DIMENSIONS
# ---------------------------------------------------------------------


class Actor(Base):
    """A pseudonymous contributor. `actor_hash` is the only identifier."""

    __tablename__ = "actor"
    __table_args__ = (
        CheckConstraint("role_band IN ('junior','mid','senior','staff')"),
        CheckConstraint("tenure_bucket IN ('lt_6m','6m_2y','gt_2y')"),
        CheckConstraint("band_basis IN ('inferred','stated')"),
    )

    actor_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    role_band: Mapped[str] = mapped_column(Text, nullable=False)
    tenure_bucket: Mapped[str] = mapped_column(Text, nullable=False)
    first_seen: Mapped[dt.datetime | None] = mapped_column(TZDateTime)
    # 'inferred' for every row we will ever have. The UI renders "inferred
    # band", never "band".
    band_basis: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="inferred"
    )


class RateCard(Base):
    __tablename__ = "rate_card"
    __table_args__ = (CheckConstraint("hourly > 0"),)

    role_band: Mapped[str] = mapped_column(Text, primary_key=True)
    hourly: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default="INR")
    # Fully-loaded multiplier. State it on screen, never bury it.
    loading: Mapped[Decimal] = mapped_column(
        Numeric, nullable=False, server_default="1.30"
    )
    # Public URL + retrieval date, rendered on screen.
    source: Mapped[str] = mapped_column(Text, nullable=False)


class WorkItem(Base):
    """The case ID of the process. One row per unit of work."""

    __tablename__ = "work_item"
    __table_args__ = (
        CheckConstraint("case_source IN ('ticket_key','issue','pr')"),
        Index("idx_work_item_sprint", "sprint"),
        Index("idx_work_item_jira", "jira_key"),
    )

    work_item_id: Mapped[str] = mapped_column(Text, primary_key=True)
    repo: Mapped[str] = mapped_column(Text, nullable=False)
    component: Mapped[str | None] = mapped_column(Text)
    epic: Mapped[str | None] = mapped_column(Text)
    opened_at: Mapped[dt.datetime | None] = mapped_column(TZDateTime)
    closed_at: Mapped[dt.datetime | None] = mapped_column(TZDateTime)
    source_ref: Mapped[str | None] = mapped_column(Text)
    case_source: Mapped[str] = mapped_column(Text, nullable=False, server_default="pr")
    sprint: Mapped[int | None] = mapped_column(Integer)
    jira_key: Mapped[str | None] = mapped_column(Text)
    issue_type: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[str | None] = mapped_column(Text)
    resolution: Mapped[str | None] = mapped_column(Text)
    parent_key: Mapped[str | None] = mapped_column(Text)
    jira_created_at: Mapped[dt.datetime | None] = mapped_column(TZDateTime)
    jira_resolved_at: Mapped[dt.datetime | None] = mapped_column(TZDateTime)


# ---------------------------------------------------------------------
# 3. THE EVENT LOG
# ---------------------------------------------------------------------

#: The complete activity vocabulary. Nothing else is ever written to
#: event_log.activity. Mirrors the CHECK constraint in docs/schema.sql and the
#: canonical list in .claude/CLAUDE.md.
ACTIVITIES: tuple[str, ...] = (
    "commit",
    "review_requested",
    "review",
    "changes_requested",
    "approved",
    "merged",
    "reopened",
    "force_push",
    "ci_run",
    "deploy",
    "ticket_created",
    "ticket_started",
    "ticket_in_review",
    "ticket_resolved",
    "ticket_closed",
    "ticket_reopened",
    "meeting",
)

#: Allowed values of event_log.source. Drives the observed-vs-modelled badge.
EVENT_SOURCES: tuple[str, ...] = ("github", "jira", "synthetic")


class EventLog(Base):
    """Everything hangs off this table."""

    __tablename__ = "event_log"
    __table_args__ = (
        CheckConstraint("source IN ('github','jira','synthetic')"),
        CheckConstraint(
            "activity IN ("
            "'commit','review_requested','review','changes_requested','approved',"
            "'merged','reopened','force_push','ci_run','deploy',"
            "'ticket_created','ticket_started','ticket_in_review',"
            "'ticket_resolved','ticket_closed','ticket_reopened','meeting')"
        ),
        Index("idx_event_case", "work_item_id", "ts"),
        Index("idx_event_actor", "actor_hash", "ts"),
        Index("idx_event_activity", "activity", "ts"),
        Index("idx_event_source", "source"),
    )

    event_id: Mapped[str] = mapped_column(Text, primary_key=True)
    work_item_id: Mapped[str] = mapped_column(
        Text, ForeignKey("work_item.work_item_id"), nullable=False
    )
    # NULLABLE on purpose: a CI run has no human behind it.
    actor_hash: Mapped[str | None] = mapped_column(Text, ForeignKey("actor.actor_hash"))
    activity: Mapped[str] = mapped_column(Text, nullable=False)
    ts: Mapped[dt.datetime] = mapped_column(TZDateTime, nullable=False)
    # Inferred from session boundaries, never measured. Never from lines of code.
    duration_s: Mapped[Decimal | None] = mapped_column(Numeric)
    source: Mapped[str] = mapped_column(Text, nullable=False, server_default="github")
    attrs: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


# ---------------------------------------------------------------------
# 4. COST
# ---------------------------------------------------------------------


class WorkSession(Base):
    """An inferred stretch of focused work, derived from event timestamps."""

    __tablename__ = "work_session"

    session_id: Mapped[str] = mapped_column(Text, primary_key=True)
    actor_hash: Mapped[str] = mapped_column(
        Text, ForeignKey("actor.actor_hash"), nullable=False
    )
    work_item_id: Mapped[str] = mapped_column(
        Text, ForeignKey("work_item.work_item_id"), nullable=False
    )
    started_at: Mapped[dt.datetime] = mapped_column(TZDateTime, nullable=False)
    ended_at: Mapped[dt.datetime] = mapped_column(TZDateTime, nullable=False)
    hours: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    n_events: Mapped[int | None] = mapped_column(Integer)


class CostEvent(Base):
    __tablename__ = "cost_event"
    __table_args__ = (
        CheckConstraint(
            "basis IN ('session_inferred','ci_runner','ai_tokens','meeting')"
        ),
        Index("idx_cost_case", "work_item_id"),
        Index("idx_cost_basis", "basis"),
    )

    cost_event_id: Mapped[str] = mapped_column(Text, primary_key=True)
    # Nullable: meeting and token costs attach to a case, not to one event.
    event_id: Mapped[str | None] = mapped_column(Text, ForeignKey("event_log.event_id"))
    work_item_id: Mapped[str] = mapped_column(
        Text, ForeignKey("work_item.work_item_id"), nullable=False
    )
    actor_hash: Mapped[str | None] = mapped_column(Text, ForeignKey("actor.actor_hash"))
    hours: Mapped[Decimal | None] = mapped_column(Numeric)
    rate_band: Mapped[Decimal | None] = mapped_column(Numeric)
    cost: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    # Every money figure carries how it was derived. 'session_inferred' is an
    # estimate; the UI must say so.
    basis: Mapped[str] = mapped_column(Text, nullable=False)


class CiRun(Base):
    __tablename__ = "ci_run"
    __table_args__ = (Index("idx_ci_sha", "head_sha"),)

    run_id: Mapped[str] = mapped_column(Text, primary_key=True)
    # Nullable: most runs do not map to a case.
    work_item_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("work_item.work_item_id")
    )
    repo: Mapped[str] = mapped_column(Text, nullable=False)
    head_sha: Mapped[str | None] = mapped_column(Text)
    ts: Mapped[dt.datetime] = mapped_column(TZDateTime, nullable=False)
    runner_minutes: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    conclusion: Mapped[str | None] = mapped_column(Text)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")


class CalendarEvent(Base):
    """Synthetic. Metadata only BY CONSTRUCTION — no title, no attendee names.

    The absence of those columns is the privacy guarantee. Do not add them.
    """

    __tablename__ = "calendar_event"

    meeting_id: Mapped[str] = mapped_column(Text, primary_key=True)
    work_item_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("work_item.work_item_id")
    )
    project: Mapped[str | None] = mapped_column(Text)
    ts: Mapped[dt.datetime] = mapped_column(TZDateTime, nullable=False)
    duration_min: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    attendee_count: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="synthetic"
    )


class AiUsage(Base):
    """No actor_hash column, deliberately and permanently.

    Per-person AI usage is more sensitive than commit data, not less, and must
    not be queryable at any aggregation. Do not add one.
    """

    __tablename__ = "ai_usage"

    usage_id: Mapped[str] = mapped_column(Text, primary_key=True)
    work_item_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("work_item.work_item_id")
    )
    ts: Mapped[dt.datetime] = mapped_column(TZDateTime, nullable=False)
    vendor: Mapped[str] = mapped_column(Text, nullable=False)
    tokens_in: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    tokens_out: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    cost: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    source: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="synthetic"
    )


# ---------------------------------------------------------------------
# 5. DERIVED
# ---------------------------------------------------------------------


class Variant(Base):
    __tablename__ = "variant"

    variant_id: Mapped[str] = mapped_column(Text, primary_key=True)
    repo: Mapped[str] = mapped_column(Text, nullable=False)
    activity_sequence: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    n_cases: Mapped[int] = mapped_column(Integer, nullable=False)
    total_cost: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    cost_share: Mapped[Decimal | None] = mapped_column(Numeric)
    is_modal: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )


class CodeChurn(Base):
    __tablename__ = "code_churn"

    churn_id: Mapped[str] = mapped_column(Text, primary_key=True)
    work_item_id: Mapped[str] = mapped_column(
        Text, ForeignKey("work_item.work_item_id"), nullable=False
    )
    path: Mapped[str] = mapped_column(Text, nullable=False)
    rewritten_by: Mapped[str | None] = mapped_column(
        Text, ForeignKey("work_item.work_item_id")
    )
    days_survived: Mapped[Decimal | None] = mapped_column(Numeric)
    hours_wasted: Mapped[Decimal | None] = mapped_column(Numeric)


class BacktestResult(Base):
    """Blind backtest output. Time-based splits only — a random split leaks
    the future on a temporal process."""

    __tablename__ = "backtest_result"

    run_id: Mapped[str] = mapped_column(Text, primary_key=True)
    trained_through: Mapped[int] = mapped_column(Integer, nullable=False)
    predicted_sprint: Mapped[int] = mapped_column(Integer, nullable=False)
    n_items: Mapped[int | None] = mapped_column(Integer)
    mape: Mapped[Decimal | None] = mapped_column(Numeric)
    band_coverage: Mapped[Decimal | None] = mapped_column(Numeric)
    created_at: Mapped[dt.datetime] = mapped_column(
        TZDateTime, nullable=False, server_default=func.now()
    )
