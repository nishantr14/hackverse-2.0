"""
DB session management.
Owner: shared infra.
Phase: Tier 0.

Two separate roles, per the privacy rules in .claude/CLAUDE.md:
- a read-only role used by the API (this module's default),
- a separate write/loader role used only by app/ingestion + app/synthetic.
Never let the API session have write access to actor/event_log tables.
"""

# TODO: SQLAlchemy engine/session factory reading DB_URL from
# app.config, using the read-only Postgres role for API queries.
