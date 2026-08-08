"""The fourteen-item event-log verification report.

Every number below is a SQL query printed with the query's own result. No
figure here is computed in Python from a partial fetch, because a verification
report that disagrees with the database is worse than no report.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

#: Item 11. Exact duplicates are impossible — event_id is the primary key. The
#: duplicate that CAN happen is the same real-world fact landing twice under
#: two ids because two sources both described it, so each source is checked
#: against the natural key of its own evidence.
#:
#: Deliberately NOT grouped on (case, activity, timestamp, actor): three
#: reviewers requested in one second, and the 28 workflows apache/kafka fires
#: on a single commit, are all distinct facts. That coarser query reports
#: 1,583 "duplicates" on this database and every one of them is real data.
EVIDENCE_DUPLICATES = """
SELECT 'commit (by sha)' AS evidence, COALESCE(SUM(n - 1), 0) AS duplicates FROM (
    SELECT COUNT(*) AS n FROM v_event_log
     WHERE activity = 'commit' AND attrs ? 'sha'
     GROUP BY attrs->>'sha' HAVING COUNT(*) > 1) d
UNION ALL
SELECT 'ci_run (by run_id)', COALESCE(SUM(n - 1), 0) FROM (
    SELECT COUNT(*) AS n FROM v_event_log
     WHERE activity = 'ci_run'
     GROUP BY attrs->>'run_id' HAVING COUNT(*) > 1) d
UNION ALL
SELECT 'pr event (by pr+activity+ts+who)', COALESCE(SUM(n - 1), 0) FROM (
    SELECT COUNT(*) AS n FROM v_event_log
     WHERE ingest_source = 'github_graphql'
     GROUP BY attrs->>'pr_number', activity, ts,
              COALESCE(resource, ''), COALESCE(attrs->>'requested_reviewer', '')
     HAVING COUNT(*) > 1) d
UNION ALL
SELECT 'jira transition (by key+ts+target)', COALESCE(SUM(n - 1), 0) FROM (
    SELECT COUNT(*) AS n FROM v_event_log
     WHERE ingest_source = 'asf_jira' AND activity <> 'ticket_created'
     GROUP BY jira_key, ts, attrs->>'to_status', COALESCE(resource, '')
     HAVING COUNT(*) > 1) d
"""

#: Item 14. Ordering by timestamp is chronologically valid by construction, so
#: the check that earns its place is a LOGICAL one: orderings that cannot
#: happen inside a single unit of work, each a symptom of two different cases
#: having been collapsed into one by a bad join.
#:
#: SCOPE IS THE WHOLE POINT. Unscoped, `approved after merged` fires on 178
#: cases here and `merged before first commit` on 11 — and every one is a Jira
#: ticket with more than one pull request against it, where PR #2 is committed
#: after PR #1 merged. Perfectly ordered work that a case-level check calls
#: broken. Both are therefore scoped to the unit the ordering must hold within.
ORDER_VIOLATIONS = """
WITH pr_count AS (
    SELECT case_id, COUNT(DISTINCT attrs->>'pr_number') AS n_prs
      FROM v_event_log WHERE attrs ? 'pr_number' GROUP BY 1
)
SELECT 'merged before first commit (1-PR cases)' AS violation, COUNT(*) AS n
  FROM v_case_evidence e LEFT JOIN pr_count p USING (case_id)
 WHERE e.merged_at IS NOT NULL AND e.first_commit_at IS NOT NULL
   AND e.merged_at < e.first_commit_at
   AND COALESCE(p.n_prs, 1) = 1
UNION ALL
SELECT 'ticket created after first commit', COUNT(*)
  FROM v_case_evidence
 WHERE ticket_created_at IS NOT NULL AND first_commit_at IS NOT NULL
   AND ticket_created_at > first_commit_at
UNION ALL
SELECT 'case spanning two repositories', COUNT(*) FROM (
    SELECT case_id FROM v_event_log
     WHERE attrs->>'repo' IS NOT NULL
     GROUP BY case_id HAVING COUNT(DISTINCT attrs->>'repo') > 1) x
"""

#: Orderings that are odd but are what the API actually returned. These are
#: reported and NOT counted as failures, because the mapper reproducing its
#: source faithfully is the correct behaviour — inventing a tidier timestamp
#: to make a check pass would be the bug.
SOURCE_ANOMALIES = """
SELECT 'approved after its own PR merged' AS anomaly, COUNT(*) AS n,
       MAX(ROUND((EXTRACT(EPOCH FROM (a.ts - m.ts)) / 86400)::numeric, 0)) AS worst_days
  FROM v_event_log a
  JOIN v_event_log m
    ON m.case_id = a.case_id AND m.activity = 'merged'
   AND m.attrs->>'pr_number' = a.attrs->>'pr_number'
 WHERE a.activity = 'approved' AND a.ts > m.ts
UNION ALL
SELECT 'event timestamped before its case opened', COUNT(*),
       MAX(ROUND((EXTRACT(EPOCH FROM (w.opened_at - e.ts)) / 86400)::numeric, 0))
  FROM v_event_log e JOIN work_item w ON w.work_item_id = e.case_id
 WHERE w.opened_at IS NOT NULL AND e.ts < w.opened_at
   AND e.ingest_source <> 'asf_jira'
"""


@dataclass
class Row:
    label: str
    value: Any


def _scalar(session: Session, sql: str) -> Any:
    return session.execute(text(sql)).scalar()


def _rows(session: Session, sql: str) -> list[tuple]:
    return [tuple(r) for r in session.execute(text(sql))]


def _table(title: str, rows: list[tuple], width: int = 30) -> None:
    print(f"\n  {title}")
    if not rows:
        print("    (none)")
        return
    for row in rows:
        head = str(row[0])
        rest = "  ".join(f"{v:>10,}" if isinstance(v, int) else str(v) for v in row[1:])
        print(f"    {head:<{width}} {rest}")


def print_report(session: Session) -> dict[str, Any]:
    """Print the report and return the figures, so tests can assert on them."""
    out: dict[str, Any] = {}

    print("\n" + "=" * 68)
    print("CANONICAL EVENT LOG — verification")
    print("=" * 68)

    out["events"] = _scalar(session, "SELECT count(*) FROM v_event_log")
    out["cases"] = _scalar(session, "SELECT count(*) FROM v_case_evidence")
    out["cases_with_events"] = _scalar(
        session, "SELECT count(*) FROM v_case_evidence WHERE n_events > 0"
    )
    print(f"\n  1. total canonical events           {out['events']:>10,}")
    print(f"  2. unique cases (work items)        {out['cases']:>10,}")
    print(f"     of which have >= 1 event         {out['cases_with_events']:>10,}")

    _table(
        "3. events by activity",
        _rows(
            session,
            "SELECT activity, count(*), count(*) FILTER (WHERE NOT in_window) "
            "FROM v_event_log GROUP BY 1 ORDER BY 2 DESC",
        ),
    )
    print("       (second column = events retained from BEFORE the window)")

    _table(
        "4. events by source",
        _rows(
            session,
            "SELECT ingest_source, count(*) FROM v_event_log GROUP BY 1 ORDER BY 2 DESC",
        ),
    )
    _table(
        "5. events by repository",
        _rows(session, "SELECT repo, count(*) FROM v_event_log GROUP BY 1 ORDER BY 2 DESC"),
    )

    out["git_pr_jira"] = _scalar(
        session,
        "SELECT count(*) FROM v_case_evidence WHERE has_git AND has_pr AND has_jira",
    )
    out["git_pr_ci"] = _scalar(
        session,
        "SELECT count(*) FROM v_case_evidence WHERE has_git AND has_pr AND has_ci",
    )
    print(f"\n  6. cases with Git + PR + Jira       {out['git_pr_jira']:>10,}")
    print(f"  7. cases with Git + PR + CI         {out['git_pr_ci']:>10,}")

    # "Unmatched" for a PR does not mean it has no case — the fallback chain
    # guarantees every PR gets one. It means the case has no commit evidence,
    # which is the join that can actually fail.
    out["prs_total"] = _scalar(
        session,
        "SELECT count(*) FROM raw_payload "
        "WHERE source='github_graphql' AND entity_type='pull_request'",
    )
    out["prs_without_git"] = _scalar(
        session,
        "SELECT count(*) FROM v_case_evidence WHERE has_pr AND NOT has_git",
    )
    print(f"\n  8. PR payloads landed               {out['prs_total']:>10,}")
    print(f"     PR cases with no git commit      {out['prs_without_git']:>10,}")

    out["ci_total"] = _scalar(session, "SELECT count(*) FROM ci_run")
    out["ci_unmatched"] = _scalar(
        session, "SELECT count(*) FROM ci_run WHERE work_item_id IS NULL"
    )
    share = out["ci_unmatched"] / out["ci_total"] if out["ci_total"] else 0
    print(f"\n  9. CI runs landed                   {out['ci_total']:>10,}")
    print(f"     unmatched (work_item_id NULL)    {out['ci_unmatched']:>10,}  ({share:.1%})")
    print("     kept, not dropped: head_sha is often a merge ref absent from")
    print("     a shallow clone. event_log.work_item_id is NOT NULL so these")
    print("     stay in ci_run rather than becoming events.")

    out["jira_keys"] = _scalar(
        session,
        "SELECT count(*) FROM raw_payload "
        "WHERE source='asf_jira' AND entity_type='issue'",
    )
    out["jira_git_overlap"] = _scalar(
        session,
        "SELECT count(*) FROM v_case_evidence WHERE has_jira AND has_git",
    )
    pct = out["jira_git_overlap"] / out["jira_keys"] if out["jira_keys"] else 0
    print(f"\n 10. Jira issues landed              {out['jira_keys']:>10,}")
    print(f"     also carrying git commits        {out['jira_git_overlap']:>10,}  ({pct:.1%})")

    dupes = _rows(session, EVIDENCE_DUPLICATES)
    out["duplicates_by_evidence"] = {name: int(n) for name, n in dupes}
    out["duplicates"] = sum(int(n) for _, n in dupes)
    _table("11. duplicate events, by evidence key", dupes, width=36)
    print("     event_id is a primary key, so exact duplicates cannot exist;")
    print("     this asks whether one real fact landed twice under two ids.")

    out["missing_ts"] = _scalar(session, "SELECT count(*) FROM event_log WHERE ts IS NULL")
    out["missing_actor"] = _scalar(
        session, "SELECT count(*) FROM v_event_log WHERE resource IS NULL"
    )
    _table(
        "13. events with no actor_hash, by reason",
        _rows(
            session,
            """
            SELECT CASE
                     WHEN activity IN ('ci_run','deploy') THEN 'no human: CI'
                     WHEN activity = 'ticket_created'
                       THEN 'no human: Jira spine carries no reporter'
                     WHEN attrs->>'actor_absent' = 'bot' THEN 'bot, never an actor'
                     WHEN attrs->>'actor_absent' = 'unattributed'
                       THEN 'deleted account: API returned no author'
                     ELSE 'UNEXPLAINED — investigate'
                   END AS reason,
                   count(*)
              FROM v_event_log WHERE resource IS NULL
             GROUP BY 1 ORDER BY 2 DESC
            """,
        ),
        width=44,
    )
    out["missing_actor_unexplained"] = _scalar(
        session,
        """
        SELECT count(*) FROM v_event_log
         WHERE resource IS NULL
           AND activity NOT IN ('ci_run','deploy','ticket_created')
           AND attrs->>'actor_absent' IS NULL
        """,
    )
    print(f"\n 12. events with no timestamp        {out['missing_ts']:>10,}")
    print(f" 13. events with no actor_hash       {out['missing_actor']:>10,}")
    print(f"     unexplained (must be zero)      {out['missing_actor_unexplained']:>10,}")

    violations = _rows(session, ORDER_VIOLATIONS)
    out["order_violations"] = {name: n for name, n in violations}
    out["order_violations_total"] = sum(n for _, n in violations)
    _table("14. mapping violations (must all be zero)", violations, width=40)

    anomalies = _rows(session, SOURCE_ANOMALIES)
    out["source_anomalies"] = {name: n for name, n, _ in anomalies}
    _table("    source anomalies (count, worst gap in days)", anomalies, width=40)
    print("    Not failures. GitHub permits approving a merged PR, and it")
    print("    returned one review submitted 66 seconds before its own PR was")
    print("    created. Reproducing the source faithfully is correct; inventing")
    print("    a tidier timestamp to make a check pass would be the bug.")

    print("\n" + "-" * 68)
    verdict = (
        out["missing_ts"] == 0
        and out["duplicates"] == 0
        and out["missing_actor_unexplained"] == 0
        and out["order_violations_total"] == 0
        and out["events"] > 0
    )
    print("  VERDICT:", "PASS" if verdict else "FAIL — do not build on this log yet")
    print("-" * 68)
    out["pass"] = verdict
    return out
