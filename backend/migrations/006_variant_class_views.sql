-- =====================================================================
-- 006 — variant CLASSES, and the cost-weighted edges within each.
--
-- WHY THIS EXISTS
--   v_variants (004) returns the true variant of every case: the exact
--   ordered activity sequence, hashed. That is the honest process-mining
--   answer and there are thousands of them — which is precisely why it
--   cannot drive a picture. A map with 3,000 colours is not a map.
--
--   So this collapses cases into three SEMANTIC classes, chosen because
--   each one names a thing a team can actually act on:
--
--     rework_loop     someone asked for changes. Work was redone.
--     triple_review   three or more review passes and no changes ever
--                     requested — review effort that found nothing.
--     happy_path      everything else.
--
--   Precedence matters: a case that both had changes requested AND went
--   round three times is a rework_loop, because the rework is the
--   actionable fact. Classes are mutually exclusive so the shares sum to 1.
--
-- The exact-sequence variants are NOT replaced by this. Both are served;
-- /process/variants keeps returning the real ones.
--
-- Idempotent: safe to re-run.
-- =====================================================================

CREATE OR REPLACE VIEW v_variant_class AS
SELECT
    e.work_item_id,
    CASE
        WHEN count(*) FILTER (WHERE e.activity = 'changes_requested') > 0
            THEN 'rework_loop'
        WHEN count(*) FILTER (WHERE e.activity = 'review') >= 3
            THEN 'triple_review'
        ELSE 'happy_path'
    END AS variant_class
FROM event_log e
GROUP BY 1;


-- ---------------------------------------------------------------------
-- v_edges_by_variant — one row per (class, transition).
--
-- COST APPORTIONMENT, STATED: a case's cost is divided evenly across the
-- transitions that case actually makes, and each transition's share is
-- summed into its edge. A case costing 60,000 that makes 6 transitions
-- contributes 10,000 to each. It is an apportionment, not a measurement —
-- we know what the case cost, never what each individual step of it did.
-- Even division is the only split that does not smuggle in an assumption
-- about which steps are expensive, which is the very thing the map is
-- being used to discover.
-- ---------------------------------------------------------------------

CREATE OR REPLACE VIEW v_edges_by_variant AS
WITH labeled AS (
    SELECT
        t.work_item_id,
        t.source_activity,
        t.target_activity,
        vc.variant_class,
        w.repo
    FROM v_transitions_human t
    JOIN work_item      w  ON w.work_item_id  = t.work_item_id
    JOIN v_variant_class vc ON vc.work_item_id = t.work_item_id
    WHERE t.target_activity IS NOT NULL
),
per_case AS (
    SELECT work_item_id, count(*) AS n_transitions
    FROM labeled
    GROUP BY 1
)
SELECT
    l.repo,
    l.variant_class,
    l.source_activity,
    l.target_activity,
    count(*)                          AS n_transitions,
    count(DISTINCT l.work_item_id)    AS n_cases,
    COALESCE(
        SUM(COALESCE(cc.total_cost, 0) / NULLIF(pc.n_transitions, 0)), 0
    )                                 AS cost_rupees
FROM labeled l
JOIN per_case pc     ON pc.work_item_id = l.work_item_id
LEFT JOIN v_case_cost cc ON cc.work_item_id = l.work_item_id
GROUP BY 1, 2, 3, 4;


-- ---------------------------------------------------------------------
-- v_variant_class_summary — each class's share of work items and of cost.
-- The screen's whole argument is that these two shares DISAGREE.
-- ---------------------------------------------------------------------

CREATE OR REPLACE VIEW v_variant_class_summary AS
WITH scoped AS (
    SELECT
        vc.variant_class,
        w.repo,
        w.work_item_id,
        COALESCE(cc.total_cost, 0) AS cost
    FROM v_variant_class vc
    JOIN work_item w         ON w.work_item_id  = vc.work_item_id
    LEFT JOIN v_case_cost cc ON cc.work_item_id = vc.work_item_id
),
totals AS (
    SELECT count(*)::numeric AS all_items, NULLIF(SUM(cost), 0) AS all_cost
    FROM scoped
)
SELECT
    s.variant_class,
    count(*)                                   AS n_cases,
    SUM(s.cost)                                AS total_cost,
    count(*)::numeric / t.all_items            AS share_of_work_items,
    COALESCE(SUM(s.cost) / t.all_cost, 0)      AS share_of_cost
FROM scoped s
CROSS JOIN totals t
GROUP BY s.variant_class, t.all_items, t.all_cost;


GRANT SELECT ON v_variant_class, v_edges_by_variant, v_variant_class_summary
TO esi_app;
