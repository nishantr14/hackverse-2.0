-- =====================================================================
-- 005 — the two views the /spend and /simulate routers read.
--
-- Both are additive. docs/schema.sql is frozen and untouched; these live
-- here for the same reason v_event_log (002) and the process/waste views
-- (004) do — a view added after the freeze belongs in a migration.
--
-- Idempotent: safe to re-run.
-- =====================================================================


-- ---------------------------------------------------------------------
-- v_case_spend — per work item, with the author/review split the spend
-- screen draws its hours bar from.
--
-- WHY THE SPLIT IS APPORTIONED RATHER THAN MEASURED
--   work_session rows are stretches of one actor's activity on one case.
--   They carry hours, but not "what kind of work" — a session that spans a
--   commit and a review is one row. So the split is the actor's own event
--   mix on that case: if 3 of their 10 events there were reviews, 30% of
--   their hours on it are review hours. It is an apportionment, it uses
--   the same event-count logic session_inference already uses to split a
--   session across work items, and it is the only signal available short
--   of inventing one.
--
--   review = the acts of reviewing. review_requested is deliberately NOT
--   in the list: requesting a review is the author asking, not the
--   reviewer working.
--
-- GRAIN NOTE: this is per work item, the same grain as v_case_cost, which
-- the frozen schema already grants to esi_app complete with total_hours
-- and n_actors. This adds the split at that existing grain; it does not
-- open a new one. Component-level spend keeps its k floor in
-- v_spend_by_component.
-- ---------------------------------------------------------------------

CREATE OR REPLACE VIEW v_case_spend AS
WITH mix AS (
    SELECT
        e.work_item_id,
        e.actor_hash,
        count(*) FILTER (
            WHERE e.activity IN ('review', 'approved', 'changes_requested')
        )::numeric AS review_events,
        count(*)::numeric AS all_events
    FROM event_log e
    WHERE e.actor_hash IS NOT NULL
      AND e.activity <> 'ci_run'
    GROUP BY 1, 2
),
labour AS (
    SELECT
        c.work_item_id,
        c.actor_hash,
        SUM(c.hours) AS hours
    FROM cost_event c
    WHERE c.basis = 'session_inferred'
    GROUP BY 1, 2
),
split AS (
    SELECT
        l.work_item_id,
        SUM(l.hours * (1 - COALESCE(m.review_events / NULLIF(m.all_events, 0), 0)))
            AS author_hours,
        SUM(l.hours * COALESCE(m.review_events / NULLIF(m.all_events, 0), 0))
            AS review_hours
    FROM labour l
    LEFT JOIN mix m
           ON m.work_item_id = l.work_item_id
          AND m.actor_hash   = l.actor_hash
    GROUP BY 1
),
priced AS (
    SELECT
        work_item_id,
        SUM(cost) AS total_cost,
        -- Labour kept separate on purpose. author_hours + review_hours are
        -- LABOUR hours only, so a blended rate computed as total_cost over
        -- those hours silently folds meeting and token spend into an
        -- engineer's hourly rate and lands above the staff band. The screen
        -- needs both numbers to avoid that.
        SUM(cost) FILTER (WHERE basis = 'session_inferred') AS labour_cost
    FROM cost_event
    GROUP BY 1
)
SELECT
    w.work_item_id,
    w.repo,
    COALESCE(w.component, 'unassigned') AS component,
    w.sprint,
    COALESCE(s.author_hours, 0) AS author_hours,
    COALESCE(s.review_hours, 0) AS review_hours,
    COALESCE(p.total_cost, 0)   AS cost,
    COALESCE(p.labour_cost, 0)  AS labour_cost
FROM work_item w
LEFT JOIN split  s ON s.work_item_id = w.work_item_id
LEFT JOIN priced p ON p.work_item_id = w.work_item_id;


-- ---------------------------------------------------------------------
-- v_component_capacity — the observed inputs the simulator reasons over.
--
-- Everything here is measured. The simulator's ASSUMPTIONS (linear
-- capacity, ramp-up curve) live in app/models/simulator.py where they are
-- named and printed, never buried in SQL.
--
-- `cost` carries the same k-anonymity floor as v_spend_by_component: a
-- component worked by fewer than k people has its spend withheld rather
-- than dropped, so the UI can say a value was suppressed and print the
-- threshold. throughput and engineer counts are not per-person figures
-- and are not suppressed.
-- ---------------------------------------------------------------------

CREATE OR REPLACE VIEW v_component_capacity AS
WITH scoped AS (
    SELECT
        work_item_id,
        repo,
        COALESCE(component, 'unassigned') AS component,
        opened_at,
        closed_at
    FROM work_item
),
people AS (
    SELECT
        s.repo,
        s.component,
        count(DISTINCT e.actor_hash) AS n_engineers
    FROM scoped s
    JOIN event_log e ON e.work_item_id = s.work_item_id
    WHERE e.actor_hash IS NOT NULL
    GROUP BY 1, 2
),
flow AS (
    SELECT
        repo,
        component,
        count(*)                                        AS total_items,
        count(*) FILTER (WHERE closed_at IS NOT NULL)   AS closed_items,
        count(*) FILTER (WHERE closed_at IS NULL)       AS open_items,
        GREATEST(
            EXTRACT(EPOCH FROM (max(closed_at) - min(closed_at))) / 604800.0,
            1
        ) AS span_weeks
    FROM scoped
    GROUP BY 1, 2
),
-- Week-to-week variability of delivery. This is what the confidence band
-- is derived from: a component that ships 2 items one week and 20 the next
-- cannot be forecast as tightly as one that ships 6 every week.
weekly AS (
    SELECT
        repo,
        component,
        COALESCE(
            stddev_samp(n) / NULLIF(avg(n), 0), 0
        ) AS throughput_cv
    FROM (
        SELECT
            repo,
            component,
            date_trunc('week', closed_at) AS wk,
            count(*) AS n
        FROM scoped
        WHERE closed_at IS NOT NULL
        GROUP BY 1, 2, 3
    ) t
    GROUP BY 1, 2
),
spend AS (
    SELECT
        s.repo,
        s.component,
        SUM(c.cost)  AS total_cost,
        SUM(c.hours) AS total_hours
    FROM scoped s
    JOIN cost_event c ON c.work_item_id = s.work_item_id
    GROUP BY 1, 2
)
SELECT
    f.repo,
    f.component,
    COALESCE(p.n_engineers, 0)          AS n_engineers,
    f.total_items,
    f.closed_items,
    f.open_items,
    f.span_weeks,
    f.closed_items / f.span_weeks       AS items_per_week,
    COALESCE(w.throughput_cv, 0)        AS throughput_cv,
    COALESCE(p.n_engineers, 0) < k_floor() AS suppressed,
    CASE
        WHEN COALESCE(p.n_engineers, 0) >= k_floor() THEN sp.total_cost
    END                                 AS cost,
    COALESCE(sp.total_hours, 0)         AS hours,
    k_floor()                           AS k_applied
FROM flow f
LEFT JOIN people p ON p.repo = f.repo AND p.component = f.component
LEFT JOIN weekly w ON w.repo = f.repo AND w.component = f.component
LEFT JOIN spend  sp ON sp.repo = f.repo AND sp.component = f.component;


GRANT SELECT ON v_case_spend, v_component_capacity TO esi_app;
