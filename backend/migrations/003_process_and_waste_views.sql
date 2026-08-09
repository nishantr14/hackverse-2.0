-- =====================================================================
-- 003 — process discovery (P6) and waste-detector support views (P7).
-- Owner: Diljit (cost/waste lane).
--
-- docs/schema.sql is FROZEN and migrations/002_canonical_event_log.sql is
-- Dipen's. Nothing here redefines anything either file already has —
-- v_transitions, v_case_sequence, v_review_latency, v_rework_pairs,
-- v_backlog_time and v_case_cost already exist and are reused below.
--
-- Applied at runtime (docker initdb only ever sees docs/schema.sql), same
-- pattern as 002: CREATE OR REPLACE throughout, safe to re-run.
-- =====================================================================

-- ---------------------------------------------------------------------
-- Process discovery. CI_RUN IS EXCLUDED FROM EVERYTHING BELOW.
--
-- Measured on this database: 7,464 cases / 3,224 distinct raw activity
-- sequences even with ci_run excluded (43%) — not the "every case is
-- unique" wall that would mean stopping here, but a real hairball without
-- the collapse-and-rank steps below.
-- ---------------------------------------------------------------------

-- Consecutive identical activities on one case become one node with a
-- repeat count, so a PR with six review rounds is one "review x6" node
-- rather than six distinct variants that differ only in how many times a
-- human clicked the same button. Classic gaps-and-islands: the difference
-- between two row-number sequences is constant exactly for a run of the
-- same activity.
CREATE OR REPLACE VIEW v_collapsed_sequence AS
WITH numbered AS (
    SELECT
        case_id, activity, ts, event_id,
        ROW_NUMBER() OVER (PARTITION BY case_id ORDER BY ts, event_id)
        - ROW_NUMBER() OVER (PARTITION BY case_id, activity ORDER BY ts, event_id)
            AS run_id
    FROM v_event_log
    WHERE activity <> 'ci_run'
)
SELECT
    case_id,
    activity,
    run_id,
    MIN(ts) AS started_at,
    MAX(ts) AS ended_at,
    COUNT(*) AS repeat_count,
    ROW_NUMBER() OVER (PARTITION BY case_id ORDER BY MIN(ts)) AS node_step
FROM numbered
GROUP BY case_id, activity, run_id;

-- Directly-follows relation over the collapsed, non-CI sequence. This is
-- what the process graph draws edges from.
CREATE OR REPLACE VIEW v_transitions_human AS
SELECT
    case_id AS work_item_id,
    activity AS source_activity,
    LEAD(activity) OVER w AS target_activity,
    started_at AS source_started_at,
    LEAD(started_at) OVER w AS target_started_at,
    EXTRACT(EPOCH FROM (LEAD(started_at) OVER w - ended_at)) / 3600.0 AS gap_hours
FROM v_collapsed_sequence
WINDOW w AS (PARTITION BY case_id ORDER BY node_step);

-- Edge-level rollup. EDGE WEIGHT IS COST, NOT FREQUENCY — that's the
-- product. `case_cost_exposure` sums each distinct case's total cost
-- (v_case_cost, all four bases) once per case touching the edge, not once
-- per occurrence, so a case with the same edge twice isn't double-counted.
-- Until P5 seeds rate_card, v_case_cost only has AI-token cost to sum —
-- real, not zero, but partial; it fills in as session/CI cost lands, no
-- code change needed here.
CREATE OR REPLACE VIEW v_edges AS
WITH labeled AS (
    SELECT t.*, w.repo
    FROM v_transitions_human t
    JOIN work_item w ON w.work_item_id = t.work_item_id
    WHERE t.target_activity IS NOT NULL
),
edge_cases AS (
    SELECT DISTINCT repo, source_activity, target_activity, work_item_id
    FROM labeled
),
priced AS (
    SELECT ec.*, COALESCE(cc.total_cost, 0) AS case_cost
    FROM edge_cases ec
    LEFT JOIN v_case_cost cc ON cc.work_item_id = ec.work_item_id
)
SELECT
    l.repo,
    l.source_activity,
    l.target_activity,
    COUNT(*) AS n_transitions,
    COUNT(DISTINCT l.work_item_id) AS n_cases,
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY l.gap_hours) AS median_gap_hours,
    p.total_case_cost AS case_cost_exposure
FROM labeled l
JOIN (
    SELECT repo, source_activity, target_activity, SUM(case_cost) AS total_case_cost
    FROM priced
    GROUP BY 1, 2, 3
) p USING (repo, source_activity, target_activity)
GROUP BY l.repo, l.source_activity, l.target_activity, p.total_case_cost;

-- One row per case: its collapsed variant key, the full node sequence
-- (activity + repeat count), and the case's total cost. `v_variants`
-- below aggregates this; kept separate so the API can also answer "which
-- variant is THIS case" without re-deriving the STRING_AGG.
CREATE OR REPLACE VIEW v_case_variant AS
SELECT
    cs.case_id AS work_item_id,
    w.repo,
    STRING_AGG(
        cs.activity || CASE WHEN cs.repeat_count > 1
                             THEN 'x' || cs.repeat_count ELSE '' END,
        '>' ORDER BY cs.node_step
    ) AS variant_key,
    ARRAY_AGG(
        cs.activity || CASE WHEN cs.repeat_count > 1
                             THEN 'x' || cs.repeat_count ELSE '' END
        ORDER BY cs.node_step
    ) AS activity_sequence,
    COALESCE(cc.total_cost, 0) AS case_cost
FROM v_collapsed_sequence cs
JOIN work_item w ON w.work_item_id = cs.case_id
LEFT JOIN v_case_cost cc ON cc.work_item_id = cs.case_id
GROUP BY cs.case_id, w.repo, cc.total_cost;

-- RANKED BY COST SHARE, not frequency — the finding worth a demo beat is a
-- variant rare by case count and large by cost share, and the API must be
-- able to return exactly that ordering.
CREATE OR REPLACE VIEW v_variants AS
SELECT
    MD5(repo || '|' || variant_key) AS variant_id,
    repo,
    variant_key,
    -- One representative sequence array per variant (they're identical
    -- within a variant_key by construction; MIN just picks deterministically).
    MIN(activity_sequence) AS activity_sequence,
    COUNT(*) AS n_cases,
    SUM(case_cost) AS total_cost,
    ROUND(
        100.0 * SUM(case_cost)
        / NULLIF(SUM(SUM(case_cost)) OVER (PARTITION BY repo), 0),
        2
    ) AS cost_share_pct,
    RANK() OVER (PARTITION BY repo ORDER BY COUNT(*) DESC) = 1 AS is_modal
FROM v_case_variant
GROUP BY repo, variant_key;

-- ---------------------------------------------------------------------
-- Waste-detector support (P7).
-- ---------------------------------------------------------------------

-- Both review-latency definitions, kept as separate rows with their own
-- denominators rather than blended into one number (2,358 review_requested
-- events against 5,632 PRs — Apache reviewers often review without a
-- formal request, so the two populations are genuinely different).
CREATE OR REPLACE VIEW v_review_latency_both AS
SELECT 'requested_to_first_response' AS definition, work_item_id, latency_hours
FROM v_review_latency
UNION ALL
SELECT 'pr_opened_to_first_review' AS definition, w.work_item_id,
       EXTRACT(EPOCH FROM (first_review.ts - w.opened_at)) / 3600.0 AS latency_hours
FROM work_item w
JOIN LATERAL (
    SELECT MIN(ts) AS ts FROM event_log
     WHERE work_item_id = w.work_item_id AND activity = 'review'
) first_review ON first_review.ts IS NOT NULL
WHERE w.opened_at IS NOT NULL AND first_review.ts > w.opened_at;

-- v_backlog_time (frozen schema) has no priority/issue_type — those live on
-- work_item, not granted to the app role. Joined once here so backlog.py
-- never has to touch work_item directly.
CREATE OR REPLACE VIEW v_backlog_time_full AS
SELECT b.work_item_id, b.repo, b.component, b.backlog_hours,
       w.priority, w.issue_type
FROM v_backlog_time b
JOIN work_item w USING (work_item_id);

-- Rework, aggregated. The app role reads views only, never base tables —
-- rework.py originally joined actor + rate_card directly, which would hit
-- a Postgres permission error under esi_app since neither is granted (and
-- actor must not be, at any per-row grain). Doing the join once here,
-- server-side, single aggregate row, means the API never touches actor at
-- row grain at all.
CREATE OR REPLACE VIEW v_rework_cost AS
SELECT
    COUNT(*) AS n_pairs,
    COALESCE(SUM(rp.gap_hours), 0) AS total_hours,
    -- NULL, not 0, when nothing priced (rate_card empty or no actor match) —
    -- 0 would look like "priced at zero cost" rather than "not priced yet".
    SUM(rp.gap_hours * rc.hourly) AS total_cost,
    COUNT(*) FILTER (WHERE rc.hourly IS NULL) AS n_unpriced
FROM v_rework_pairs rp
LEFT JOIN actor a ON a.actor_hash = rp.redone_by
LEFT JOIN rate_card rc ON rc.role_band = a.role_band
WHERE rp.gap_hours >= 0;

-- CI waste's real, uncited-free signal: runner minutes on a rerun
-- (run_attempt > 1) or a failed run. The rupee/kgCO2e conversion needs a
-- cost-per-minute and a grid-factor citation neither of which exist in
-- config yet — kept out of SQL entirely so a missing citation is a
-- Python-level fail-closed error, not a silently wrong number baked into
-- a view.
CREATE OR REPLACE VIEW v_ci_waste_minutes AS
SELECT
    c.run_id, c.work_item_id, c.repo, c.attempt, c.conclusion,
    c.runner_minutes,
    c.attempt > 1 AS is_rerun,
    c.conclusion = 'failure' AS is_failure
FROM ci_run c
WHERE c.attempt > 1 OR c.conclusion = 'failure';

GRANT SELECT ON
    v_collapsed_sequence, v_transitions_human, v_edges,
    v_case_variant, v_variants,
    v_review_latency_both, v_ci_waste_minutes, v_rework_cost,
    v_backlog_time_full
TO esi_app;
