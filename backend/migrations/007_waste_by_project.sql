-- =====================================================================
-- 007 — waste split by project and component.
--
-- The detectors return one global figure each, which is the right answer
-- to "how much" and a useless answer to "where". WasteView groups by
-- project, so the same detectors are re-expressed at (repo, component)
-- grain here.
--
-- CI IS DELIBERATELY ABSENT. Its rupee conversion is kept out of SQL on
-- purpose (see the note above v_ci_waste_minutes in 004): the per-minute
-- price needs a citation, and a missing citation has to fail closed in
-- Python rather than bake a silently-wrong number into a view. The API
-- prices CI minutes per repo itself, from the same config every other
-- rupee comes from.
--
-- LATENCY CARRIES HOURS AND A NULL COST, BY DECISION. Waiting is not paid
-- engineer time — nobody is billed to watch a PR sit. Pricing idle wall
-- clock at a salary rate is how a waste number becomes indefensible, so
-- latency reports duration and refuses to convert it.
--
-- Idempotent: safe to re-run.
-- =====================================================================

CREATE OR REPLACE VIEW v_waste_by_project AS

-- Rework: changes_requested -> redo commit, priced at the redoer's band.
SELECT
    'rework'::text        AS waste_type,
    w.repo                AS repo,
    w.component           AS component,
    count(*)              AS n_items,
    COALESCE(SUM(rp.gap_hours), 0) AS hours,
    SUM(rp.gap_hours * rc.hourly)  AS cost
FROM v_rework_pairs rp
JOIN work_item w      ON w.work_item_id = rp.work_item_id
LEFT JOIN actor a     ON a.actor_hash   = rp.redone_by
LEFT JOIN rate_card rc ON rc.role_band  = a.role_band
WHERE rp.gap_hours >= 0
GROUP BY 1, 2, 3

UNION ALL

-- Meetings: already priced into cost_event, re-cut by where they landed.
SELECT
    'meeting'::text,
    repo,
    component,
    count(*),
    0,
    SUM(meeting_cost)
FROM v_case_cost
WHERE meeting_cost IS NOT NULL AND meeting_cost > 0
GROUP BY 1, 2, 3

UNION ALL

-- Review latency: hours only. cost is NULL and stays NULL.
--
-- MEDIAN, NOT SUM. Waiting happens in parallel — fifty PRs each sitting a
-- week is one week of elapsed time, not fifty. Summing them produced
-- "182,505 days", which is five centuries and is the kind of number that
-- ends an argument in the wrong direction. The median is what a
-- contributor actually experiences.
SELECT
    'latency'::text,
    w.repo,
    w.component,
    count(*),
    COALESCE(
        PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY l.latency_hours), 0
    ),
    NULL
FROM v_review_latency_both l
JOIN work_item w ON w.work_item_id = l.work_item_id
WHERE l.definition = 'pr_opened_to_first_review'
GROUP BY 1, 2, 3;


GRANT SELECT ON v_waste_by_project TO esi_app;
