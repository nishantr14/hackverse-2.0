-- Initial migration: applies docs/schema.sql verbatim.
-- Owner: shared infra. Phase: Tier 0.
-- Do not duplicate the schema here — this file should stay a thin
-- \i include of ../../docs/schema.sql (or be regenerated from it) so the
-- two never drift. docs/schema.sql is the frozen source of truth.

\ir '../../docs/schema.sql'
