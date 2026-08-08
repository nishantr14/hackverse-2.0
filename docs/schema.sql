-- =====================================================================
-- Engineering Spend Intelligence — event log schema
--
-- FROZEN after the amendment meeting. Requires all four present to change.
-- Postgres 15. Applied automatically by infra/docker-compose.yml on first boot.
--
-- THIS DATABASE CONTAINS NO IDENTITY. No login, no email, no name, no
-- individual salary. The login -> hash mapping lives in data/identity.db
-- (SQLite, gitignored) and is opened only by backend/app/ingestion/.
-- =====================================================================

-- ---------------------------------------------------------------------
-- 0. CONFIG AND PROVENANCE
-- Every knob that moves a number on screen is recorded here, so the
-- sensitivity question has an answer with a value in it.
-- ---------------------------------------------------------------------

CREATE TABLE run_config (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL,
    note    TEXT
);

INSERT INTO run_config (key, value, note) VALUES
    ('k_floor',              '5',  'k-anonymity floor enforced in views'),
    ('k_floor_fallback',     '3',  'used only if k=5 blanks the demo; printed on screen'),
    ('session_gap_min',      '90', 'max gap before a new inferred work session starts'),
    ('session_lead_in_min',  '30', 'thinking time credited before the first event'),
    ('session_daily_cap_h',  '10', 'hard cap on inferred hours per actor per day'),
    ('sprint_days',          '14', 'sprint PROXY; open source has no sprints'),
    ('meeting_hours_week',   '4',  'ASSUMPTION exposed as a UI slider, not observed data'),
    ('ci_duration_basis',    'wall_clock', 'we use updated_at - run_started_at, not billable minutes'),
    ('grid_factor_note',     '',   'citation for the kWh -> kgCO2e conversion'),
    ('rate_card_note',       '',   'citation + retrieval date for the compensation source'),
    ('data_source',          'real', 'real | fixtures');

CREATE FUNCTION k_floor() RETURNS INT
LANGUAGE sql STABLE AS
$$ SELECT COALESCE((SELECT value::int FROM run_config WHERE key = 'k_floor'), 5) $$;

-- ---------------------------------------------------------------------
-- 1. RAW LANDING
-- Land every API response before parsing. The mapper reads this table,
-- never the network, so a mapping bug costs a re-run not a re-fetch.
-- ---------------------------------------------------------------------

CREATE TABLE raw_payload (
    source        TEXT NOT NULL,   -- git_local | github_graphql | github_actions | asf_jira
    entity_type   TEXT NOT NULL,
    entity_id     TEXT NOT NULL,
    fetched_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    body          JSONB NOT NULL,
    PRIMARY KEY (source, entity_type, entity_id)
);

CREATE TABLE ingest_cursor (
    source     TEXT NOT NULL,
    scope      TEXT NOT NULL,   -- repo or jira project
    cursor     TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source, scope)
);

-- ---------------------------------------------------------------------
-- 2. DIMENSIONS
-- ---------------------------------------------------------------------

CREATE TABLE actor (
    actor_hash      TEXT PRIMARY KEY,
    role_band       TEXT NOT NULL,
    tenure_bucket   TEXT NOT NULL,
    first_seen      TIMESTAMPTZ,
    band_basis      TEXT NOT NULL DEFAULT 'inferred',
    CHECK (role_band     IN ('junior','mid','senior','staff')),
    CHECK (tenure_bucket IN ('lt_6m','6m_2y','gt_2y')),
    CHECK (band_basis    IN ('inferred','stated'))
    -- band_basis is 'inferred' for every row we will ever have. The UI must
    -- render "inferred band", never "band". Rates are public and cited;
    -- band ASSIGNMENT is a stated rule over contribution history.
);

CREATE TABLE rate_card (
    role_band   TEXT PRIMARY KEY,
    hourly      NUMERIC NOT NULL CHECK (hourly > 0),
    currency    TEXT NOT NULL DEFAULT 'INR',
    loading     NUMERIC NOT NULL DEFAULT 1.30,  -- fully-loaded multiplier, state it
    source      TEXT NOT NULL                   -- public URL + retrieval date, rendered on screen
);

CREATE TABLE work_item (
    work_item_id     TEXT PRIMARY KEY,
    repo             TEXT NOT NULL,
    component        TEXT,
    epic             TEXT,
    opened_at        TIMESTAMPTZ,
    closed_at        TIMESTAMPTZ,
    source_ref       TEXT,
    case_source      TEXT NOT NULL DEFAULT 'pr',
    sprint           INTEGER,
    jira_key         TEXT,
    issue_type       TEXT,
    priority         TEXT,
    resolution       TEXT,
    parent_key       TEXT,
    jira_created_at  TIMESTAMPTZ,
    jira_resolved_at TIMESTAMPTZ,
    CHECK (case_source IN ('ticket_key','issue','pr'))
);

CREATE INDEX idx_work_item_sprint ON work_item (sprint);
CREATE INDEX idx_work_item_jira   ON work_item (jira_key);

-- ---------------------------------------------------------------------
-- 3. THE EVENT LOG
-- Everything hangs off this table. If a query does not ultimately read
-- from here, question whether it belongs in the product.
-- ---------------------------------------------------------------------

CREATE TABLE event_log (
    event_id      TEXT PRIMARY KEY,
    work_item_id  TEXT NOT NULL REFERENCES work_item(work_item_id),
    actor_hash    TEXT REFERENCES actor(actor_hash),   -- NULLABLE: CI runs have no human
    activity      TEXT NOT NULL,
    ts            TIMESTAMPTZ NOT NULL,                -- always UTC
    duration_s    NUMERIC,                             -- inferred, never measured
    source        TEXT NOT NULL DEFAULT 'github',
    attrs         JSONB,
    CHECK (source IN ('github','jira','synthetic')),
    CHECK (activity IN (
        'commit','review_requested','review','changes_requested','approved',
        'merged','reopened','force_push','ci_run','deploy',
        'ticket_created','ticket_started','ticket_in_review',
        'ticket_resolved','ticket_closed','ticket_reopened','meeting'
    ))
);

CREATE INDEX idx_event_case     ON event_log (work_item_id, ts);
CREATE INDEX idx_event_actor    ON event_log (actor_hash, ts);
CREATE INDEX idx_event_activity ON event_log (activity, ts);
CREATE INDEX idx_event_source   ON event_log (source);

-- ---------------------------------------------------------------------
-- 4. COST
-- ---------------------------------------------------------------------

CREATE TABLE work_session (
    session_id    TEXT PRIMARY KEY,
    actor_hash    TEXT NOT NULL REFERENCES actor(actor_hash),
    work_item_id  TEXT NOT NULL REFERENCES work_item(work_item_id),
    started_at    TIMESTAMPTZ NOT NULL,
    ended_at      TIMESTAMPTZ NOT NULL,
    hours         NUMERIC NOT NULL,
    n_events      INTEGER
);

CREATE TABLE cost_event (
    cost_event_id TEXT PRIMARY KEY,
    event_id      TEXT REFERENCES event_log(event_id),        -- nullable
    work_item_id  TEXT NOT NULL REFERENCES work_item(work_item_id),
    actor_hash    TEXT REFERENCES actor(actor_hash),
    hours         NUMERIC,
    rate_band     NUMERIC,
    cost          NUMERIC NOT NULL,
    basis         TEXT NOT NULL,
    CHECK (basis IN ('session_inferred','ci_runner','ai_tokens','meeting'))
);

CREATE INDEX idx_cost_case  ON cost_event (work_item_id);
CREATE INDEX idx_cost_basis ON cost_event (basis);

CREATE TABLE ci_run (
    run_id          TEXT PRIMARY KEY,
    work_item_id    TEXT REFERENCES work_item(work_item_id),  -- nullable: most runs do not map
    repo            TEXT NOT NULL,
    head_sha        TEXT,
    ts              TIMESTAMPTZ NOT NULL,
    runner_minutes  NUMERIC NOT NULL,
    conclusion      TEXT,
    attempt         INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX idx_ci_sha ON ci_run (head_sha);

-- Synthetic. Metadata only BY CONSTRUCTION: no title, no description, no
-- attendee names. The absence of those columns is the privacy guarantee.
CREATE TABLE calendar_event (
    meeting_id     TEXT PRIMARY KEY,
    work_item_id   TEXT REFERENCES work_item(work_item_id),
    project        TEXT,
    ts             TIMESTAMPTZ NOT NULL,
    duration_min   NUMERIC NOT NULL,
    attendee_count INTEGER NOT NULL,
    source         TEXT NOT NULL DEFAULT 'synthetic'
);

-- No actor_hash column, deliberately and permanently. Per-person AI usage is
-- more sensitive than commit data, not less, and must not be queryable at any
-- aggregation. Do not add one.
CREATE TABLE ai_usage (
    usage_id      TEXT PRIMARY KEY,
    work_item_id  TEXT REFERENCES work_item(work_item_id),
    ts            TIMESTAMPTZ NOT NULL,
    vendor        TEXT NOT NULL,
    tokens_in     NUMERIC NOT NULL,
    tokens_out    NUMERIC NOT NULL,
    cost          NUMERIC NOT NULL,
    source        TEXT NOT NULL DEFAULT 'synthetic'
);

-- ---------------------------------------------------------------------
-- 5. DERIVED
-- ---------------------------------------------------------------------

CREATE TABLE variant (
    variant_id        TEXT PRIMARY KEY,
    repo              TEXT NOT NULL,
    activity_sequence TEXT[] NOT NULL,
    n_cases           INTEGER NOT NULL,
    total_cost        NUMERIC NOT NULL,
    cost_share        NUMERIC,
    is_modal          BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE code_churn (
    churn_id      TEXT PRIMARY KEY,
    work_item_id  TEXT NOT NULL REFERENCES work_item(work_item_id),
    path          TEXT NOT NULL,
    rewritten_by  TEXT REFERENCES work_item(work_item_id),
    days_survived NUMERIC,
    hours_wasted  NUMERIC
);

CREATE TABLE backtest_result (
    run_id           TEXT PRIMARY KEY,
    trained_through  INTEGER NOT NULL,
    predicted_sprint INTEGER NOT NULL,
    n_items          INTEGER,
    mape             NUMERIC,
    band_coverage    NUMERIC,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- 6. SHARED VIEWS
-- Rework, latency and cycle time are defined ONCE here. If three people
-- each write their own definition, their numbers disagree on stage.
-- ---------------------------------------------------------------------

CREATE VIEW v_event_seq AS
SELECT
    e.event_id, e.work_item_id, e.actor_hash, e.activity, e.ts, e.source,
    LEAD(e.activity)   OVER w AS next_activity,
    LEAD(e.ts)         OVER w AS next_ts,
    LEAD(e.actor_hash) OVER w AS next_actor_hash
FROM event_log e
WINDOW w AS (PARTITION BY e.work_item_id ORDER BY e.ts, e.event_id);

CREATE VIEW v_transitions AS
SELECT
    work_item_id,
    activity      AS source_activity,
    next_activity AS target_activity,
    ts            AS source_ts,
    next_ts       AS target_ts,
    EXTRACT(EPOCH FROM (next_ts - ts)) / 3600.0 AS gap_hours
FROM v_event_seq
WHERE next_activity IS NOT NULL;

CREATE VIEW v_case_sequence AS
SELECT
    w.work_item_id, w.repo, w.component, w.sprint,
    ARRAY_AGG(e.activity ORDER BY e.ts, e.event_id) AS activity_sequence,
    MD5(STRING_AGG(e.activity, '>' ORDER BY e.ts, e.event_id)) AS variant_id,
    COUNT(*) AS n_events,
    MIN(e.ts) AS started_at,
    MAX(e.ts) AS ended_at
FROM work_item w
JOIN event_log e USING (work_item_id)
GROUP BY 1,2,3,4;

CREATE VIEW v_cycle_time AS
SELECT *, EXTRACT(EPOCH FROM (merged_at - first_commit_at)) / 3600.0 AS cycle_hours
FROM (
    SELECT w.work_item_id, w.repo, w.component, w.sprint,
           MIN(e.ts) FILTER (WHERE e.activity = 'commit') AS first_commit_at,
           MIN(e.ts) FILTER (WHERE e.activity = 'merged') AS merged_at
    FROM work_item w JOIN event_log e USING (work_item_id)
    GROUP BY 1,2,3,4
) t
WHERE first_commit_at IS NOT NULL AND merged_at > first_commit_at;

CREATE VIEW v_review_latency AS
SELECT
    r.work_item_id,
    r.ts AS requested_at,
    MIN(a.ts) AS responded_at,
    EXTRACT(EPOCH FROM (MIN(a.ts) - r.ts)) / 3600.0 AS latency_hours
FROM event_log r
JOIN event_log a
  ON a.work_item_id = r.work_item_id
 AND a.ts > r.ts
 AND a.activity IN ('review','approved','changes_requested')
WHERE r.activity = 'review_requested'
GROUP BY 1,2;

-- A commit that follows a changes_requested on the same case: the redo.
CREATE VIEW v_rework_pairs AS
SELECT t.work_item_id, t.source_ts AS changes_requested_at,
       t.target_ts AS redo_commit_at, t.gap_hours, e.actor_hash AS redone_by
FROM v_transitions t
JOIN event_log e
  ON e.work_item_id = t.work_item_id AND e.ts = t.target_ts AND e.activity = 'commit'
WHERE t.source_activity = 'changes_requested' AND t.target_activity = 'commit';

-- Backlog time: ticket created to first commit. Only exists once Jira lands.
-- The most Celonis-shaped finding in the product: work sitting still, costing
-- nothing, delaying everything.
CREATE VIEW v_backlog_time AS
SELECT w.work_item_id, w.repo, w.component,
       EXTRACT(EPOCH FROM (fc.first_commit_at - w.jira_created_at)) / 3600.0 AS backlog_hours
FROM work_item w
JOIN (
    SELECT work_item_id, MIN(ts) AS first_commit_at
    FROM event_log WHERE activity = 'commit' GROUP BY 1
) fc USING (work_item_id)
WHERE w.jira_created_at IS NOT NULL AND fc.first_commit_at > w.jira_created_at;

CREATE VIEW v_case_cost AS
SELECT
    w.work_item_id, w.repo, w.component, w.epic, w.sprint,
    SUM(c.cost) AS total_cost,
    SUM(c.hours) AS total_hours,
    COUNT(DISTINCT c.actor_hash) AS n_actors,
    SUM(c.cost) FILTER (WHERE c.basis = 'session_inferred') AS labour_cost,
    SUM(c.cost) FILTER (WHERE c.basis = 'ci_runner')        AS ci_cost,
    SUM(c.cost) FILTER (WHERE c.basis = 'ai_tokens')        AS ai_cost,
    SUM(c.cost) FILTER (WHERE c.basis = 'meeting')          AS meeting_cost
FROM work_item w
LEFT JOIN cost_event c USING (work_item_id)
GROUP BY 1,2,3,4,5;

-- THE PRIVACY CHOKEPOINT. Suppressed rows are RETURNED with null metrics and
-- suppressed = true, never dropped silently, so the UI can show that a value
-- was withheld and print the threshold. The app role reads views only.
CREATE VIEW v_spend_by_component AS
SELECT
    repo,
    component,
    COUNT(DISTINCT actor_hash) AS n_actors,
    COUNT(DISTINCT actor_hash) < k_floor() AS suppressed,
    CASE WHEN COUNT(DISTINCT actor_hash) >= k_floor() THEN SUM(cost) END AS cost,
    k_floor() AS k_applied
FROM cost_event c
JOIN work_item w USING (work_item_id)
GROUP BY repo, component;

-- INTERNAL ONLY. Per-actor by construction. Feeds capability index and
-- key-person exposure. NEVER grant this to the app role, never expose a route.
CREATE VIEW v_actor_component_activity AS
SELECT
    e.actor_hash, w.component,
    COUNT(*) FILTER (WHERE e.activity = 'merged') AS n_merged,
    COUNT(*) FILTER (WHERE e.activity IN ('review','approved','changes_requested')) AS n_reviews,
    MAX(e.ts) AS last_active
FROM event_log e JOIN work_item w USING (work_item_id)
WHERE e.actor_hash IS NOT NULL
GROUP BY 1,2;

-- ---------------------------------------------------------------------
-- 7. ROLES
-- "No per-person figure can be rendered" becomes a property of the schema
-- rather than a promise about discipline.
-- ---------------------------------------------------------------------

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'esi_app') THEN
        CREATE ROLE esi_app LOGIN PASSWORD 'esi_app';
    END IF;
END $$;

GRANT USAGE ON SCHEMA public TO esi_app;
GRANT SELECT ON
    v_event_seq, v_transitions, v_case_sequence, v_cycle_time,
    v_review_latency, v_rework_pairs, v_backlog_time, v_case_cost,
    v_spend_by_component,
    variant, backtest_result, rate_card, run_config
TO esi_app;
-- Deliberately NOT granted: event_log, cost_event, actor, work_session,
-- ai_usage, calendar_event, code_churn, v_actor_component_activity.
