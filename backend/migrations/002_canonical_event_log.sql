-- =====================================================================
-- 002 — canonical event-log views.
--
-- docs/schema.sql is FROZEN and already defines v_event_seq, v_transitions,
-- v_case_sequence, v_cycle_time, v_review_latency, v_rework_pairs,
-- v_backlog_time, v_case_cost, v_spend_by_component and
-- v_actor_component_activity. Nothing here redefines any of them.
--
-- This file adds the two objects the frozen schema does not have:
--   v_event_log      the Celonis-shaped log — case, activity, timestamp,
--                    resource, and the case attributes, in one place
--   v_case_evidence  which sources actually contributed to each case
--
-- Idempotent: safe to re-run.
-- =====================================================================

-- The ingestion cutoff, recorded by the normaliser so `in_window` is
-- reproducible from the database alone rather than from whatever
-- HISTORY_MONTHS happened to be on the day.
CREATE OR REPLACE FUNCTION history_cutoff() RETURNS TIMESTAMPTZ
LANGUAGE sql STABLE AS
$$ SELECT COALESCE(
       (SELECT value::timestamptz FROM run_config WHERE key = 'history_cutoff'),
       '-infinity'::timestamptz) $$;

-- ---------------------------------------------------------------------
-- THE EVENT LOG, as a process-mining tool expects to receive it.
--
--   Case ID   work_item_id
--   Activity  activity
--   Timestamp ts (UTC, the ORIGINAL source timestamp — author date for a
--             commit, createdAt for a review request, the changelog entry's
--             own created for a Jira transition. Never updated_at.)
--   Resource  actor_hash
--   Case attrs repo, component, sprint, case_source, jira_key
--
-- `source` is the schema's three-value provenance (github|jira|synthetic).
-- `ingest_source` is the connector that produced the row, which is what you
-- actually want when asking "how many events came from Jira" — git commits
-- and PR reviews both carry source='github' and are different evidence.
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW v_event_log AS
SELECT
    e.event_id,
    e.work_item_id                                    AS case_id,
    e.activity,
    e.ts,
    e.actor_hash                                      AS resource,
    e.duration_s,
    e.source,
    COALESCE(
        e.attrs->>'ingest_source',
        CASE WHEN e.activity = 'commit' THEN 'git_local' END,
        e.source
    )                                                 AS ingest_source,
    w.repo,
    w.component,
    w.sprint,
    w.case_source,
    w.jira_key,
    e.ts >= history_cutoff()                          AS in_window,
    ROW_NUMBER() OVER (
        PARTITION BY e.work_item_id ORDER BY e.ts, e.event_id
    )                                                 AS step,
    e.attrs
FROM event_log e
JOIN work_item w USING (work_item_id);

-- ---------------------------------------------------------------------
-- Which sources proved something about each case. Answers "cases with Git +
-- PR + Jira evidence" without anyone re-deriving the join four times and
-- getting four different numbers.
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW v_case_evidence AS
SELECT
    w.work_item_id AS case_id,
    w.repo,
    w.component,
    w.sprint,
    w.case_source,
    COUNT(e.event_id)                                              AS n_events,
    MIN(e.ts)                                                      AS started_at,
    MAX(e.ts)                                                      AS ended_at,
    BOOL_OR(e.ingest_source = 'git_local')                         AS has_git,
    BOOL_OR(e.ingest_source = 'github_graphql')                    AS has_pr,
    BOOL_OR(e.ingest_source = 'github_actions')                    AS has_ci,
    BOOL_OR(e.ingest_source = 'asf_jira')                          AS has_jira,
    COUNT(*) FILTER (WHERE NOT e.in_window)                        AS n_pre_window,
    MIN(e.ts) FILTER (WHERE e.activity = 'commit')                 AS first_commit_at,
    MIN(e.ts) FILTER (WHERE e.activity = 'merged')                 AS merged_at,
    MIN(e.ts) FILTER (WHERE e.activity = 'ticket_created')         AS ticket_created_at
FROM work_item w
LEFT JOIN v_event_log e ON e.case_id = w.work_item_id
GROUP BY 1,2,3,4,5;

-- The app role reads views, never base tables. Both of these are already
-- pseudonymised; neither exposes a per-person cost figure.
GRANT SELECT ON v_event_log, v_case_evidence TO esi_app;
