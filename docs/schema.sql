-- Engineering Spend Intelligence — event log schema
-- FROZEN: do not modify without all four teammates present (Nishant, Diljit,
-- Dipen, Livana). This is the contract every workstream builds against
-- before real rows exist. Copied verbatim from the bootstrap spec.

CREATE TABLE actor (
    actor_hash      TEXT PRIMARY KEY,
    role_band       TEXT NOT NULL,
    tenure_bucket   TEXT NOT NULL,
    first_seen      TIMESTAMPTZ NOT NULL
    -- no name, no email, no salary. mapping table lives outside this DB.
);

CREATE TABLE work_item (
    work_item_id    TEXT PRIMARY KEY,
    repo            TEXT NOT NULL,
    component       TEXT,
    epic            TEXT,
    opened_at       TIMESTAMPTZ,
    closed_at       TIMESTAMPTZ,
    source_ref      TEXT
);

CREATE TABLE event_log (
    event_id        TEXT PRIMARY KEY,
    work_item_id    TEXT NOT NULL REFERENCES work_item(work_item_id),
    actor_hash      TEXT NOT NULL REFERENCES actor(actor_hash),
    activity        TEXT NOT NULL,
    -- activity: commit | review_requested | review | changes_requested
    --           | merge | ci_run | deploy
    ts              TIMESTAMPTZ NOT NULL,
    duration_s      NUMERIC,        -- duration inferred, not measured
    attrs           JSONB
);

CREATE TABLE cost_event (
    event_id        TEXT NOT NULL REFERENCES event_log(event_id),
    hours           NUMERIC NOT NULL,
    rate_band       NUMERIC NOT NULL,
    cost            NUMERIC NOT NULL,
    basis           TEXT NOT NULL
    -- basis: 'session_inferred' | 'ci_runner' | 'ai_tokens' | 'meeting'
);

CREATE TABLE rate_card (
    role_band       TEXT PRIMARY KEY,
    hourly          NUMERIC NOT NULL,
    source          TEXT NOT NULL
    -- source is a public citation string, rendered in the UI
);

CREATE TABLE ci_run (
    run_id          TEXT PRIMARY KEY,
    work_item_id    TEXT NOT NULL REFERENCES work_item(work_item_id),
    ts              TIMESTAMPTZ NOT NULL,
    runner_minutes  NUMERIC NOT NULL,
    conclusion      TEXT
);

CREATE TABLE ai_usage (
    usage_id        TEXT PRIMARY KEY,
    work_item_id    TEXT NOT NULL REFERENCES work_item(work_item_id),
    ts              TIMESTAMPTZ NOT NULL,
    vendor          TEXT NOT NULL,
    tokens_in       NUMERIC NOT NULL,
    tokens_out      NUMERIC NOT NULL,
    cost            NUMERIC NOT NULL
);

CREATE TABLE variant (
    variant_id          TEXT PRIMARY KEY,
    repo                TEXT NOT NULL,
    activity_sequence   TEXT[] NOT NULL,
    n_cases             INTEGER NOT NULL,
    total_cost          NUMERIC NOT NULL
);
