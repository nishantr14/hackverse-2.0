#!/usr/bin/env python3
"""
Remove event_log rows that fall before the HISTORY_MONTHS ingestion boundary.
Owner: Dipen (normalise + models lane).

    python scripts/purge_pre_window.py              # dry run, the default
    python scripts/purge_pre_window.py --execute    # actually delete

WHY THESE ROWS EXIST
--------------------
`github_connector` pages by UPDATED_AT DESC, so a pull request created and
merged in 2023 lands in `raw_payload` the moment somebody comments on it in
2026. `normalise/event_log.py` now skips those at mapping time - the
`mergedAt or createdAt` rule in `within_window` - but that filter can only stop
NEW rows being written. `write_events` upserts and never deletes, so every
out-of-window event mapped before the filter existed is still in the table.

This removes them. It is a one-off repair of rows a previous mapping wrote,
not a step in the pipeline.

WHAT IT COSTS AND WHY THAT IS ACCEPTABLE
----------------------------------------
Everything deleted here is reconstructible: `raw_payload` still holds the
payloads, and `python -m app.normalise.event_log --no-window-filter` maps them
back. That is exactly what "land raw, then map" buys, and it is why this
script may delete from `event_log` and must never touch `raw_payload`.

THE BOUNDARY IS READ FROM THE DATABASE, NOT RECOMPUTED
------------------------------------------------------
`history_cutoff()` reads `run_config.history_cutoff`, which `record_cutoff()`
writes on every mapper run. Recomputing it from HISTORY_MONTHS and the clock
would give a boundary a few minutes different from the one the mapper used and
than the one `v_event_log.in_window` reports, and the disagreement would show
up as a handful of rows that are out-of-window by one measure and in-window by
the other. One definition, read from where the mapper put it.

ORPHANED CASES ARE REPORTED, NEVER DELETED
------------------------------------------
Deleting an event can leave a `work_item` with no events at all. Those rows
stay. A case with no events is real data - a Jira ticket that never produced
code, an open PR nobody has reviewed - and removing cases is a different
decision from removing out-of-window events. The count is printed so the
decision is visible rather than implied.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.config import get_settings
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError

#: Every row this script may delete. Deliberately the whole predicate in one
#: place: the plan, the guards and the DELETE all reference this string, so
#: what is counted and what is removed cannot drift apart.
CANDIDATE_PREDICATE = "ts < history_cutoff()"

BY_ACTIVITY = f"""
SELECT activity, count(*) AS n,
       min(ts) AS oldest, max(ts) AS newest
  FROM event_log
 WHERE {CANDIDATE_PREDICATE}
 GROUP BY 1 ORDER BY 2 DESC
"""

DELETE_SQL = f"DELETE FROM event_log WHERE {CANDIDATE_PREDICATE}"

COUNT_CANDIDATES = f"SELECT count(*) FROM event_log WHERE {CANDIDATE_PREDICATE}"

#: git_local clones HISTORY_MONTHS of history and re-applies the window to the
#: author date, so a commit event below the cutoff should be impossible. If one
#: exists the assumption behind this whole script is wrong and it must stop:
#: commit events are the only thing here that raw_payload alone cannot rebuild,
#: because re-mapping them needs the git clone as well.
COMMITS_BELOW_CUTOFF = f"""
SELECT count(*) FROM event_log
 WHERE {CANDIDATE_PREDICATE} AND activity = 'commit'
"""

#: cost_event.event_id -> event_log.event_id. Empty today; this stops that
#: being an assumption the moment Diljit's lane lands.
BLOCKING_FK = f"""
SELECT count(*) FROM cost_event c
 WHERE c.event_id IN (SELECT event_id FROM event_log WHERE {CANDIDATE_PREDICATE})
"""

#: Cases that have events now and would have none afterwards.
WOULD_ORPHAN = f"""
SELECT count(*) FROM work_item w
 WHERE EXISTS (SELECT 1 FROM event_log e WHERE e.work_item_id = w.work_item_id)
   AND NOT EXISTS (SELECT 1 FROM event_log e
                    WHERE e.work_item_id = w.work_item_id
                      AND NOT ({CANDIDATE_PREDICATE}))
"""

#: Cases that already have no events, so this script is not what emptied them.
ALREADY_EMPTY = """
SELECT count(*) FROM work_item w
 WHERE NOT EXISTS (SELECT 1 FROM event_log e WHERE e.work_item_id = w.work_item_id)
"""

WOULD_ORPHAN_BY_SOURCE = f"""
SELECT w.case_source, count(*) AS n
  FROM work_item w
 WHERE EXISTS (SELECT 1 FROM event_log e WHERE e.work_item_id = w.work_item_id)
   AND NOT EXISTS (SELECT 1 FROM event_log e
                    WHERE e.work_item_id = w.work_item_id
                      AND NOT ({CANDIDATE_PREDICATE}))
 GROUP BY 1 ORDER BY 2 DESC
"""

SURVIVING_WINDOW = f"""
SELECT min(ts), max(ts), count(*) FROM event_log WHERE NOT ({CANDIDATE_PREDICATE})
"""


class Refused(RuntimeError):
    """A safety check failed. Raised inside the write transaction to roll back."""


def scalar(conn: Connection, sql: str) -> int:
    return int(conn.execute(text(sql)).scalar_one())


def rows(conn: Connection, sql: str) -> list[tuple]:
    return [tuple(r) for r in conn.execute(text(sql))]


def cutoff_of(conn: Connection) -> object:
    return conn.execute(text("SELECT history_cutoff()")).scalar_one()


def render_plan(conn: Connection) -> int:
    """Print what would be deleted. Returns the candidate count."""
    cutoff = cutoff_of(conn)
    total = scalar(conn, "SELECT count(*) FROM event_log")
    raw = scalar(conn, "SELECT count(*) FROM raw_payload")
    cases = scalar(conn, "SELECT count(*) FROM work_item")

    print("=" * 74)
    print("PRE-WINDOW PURGE - event_log rows older than the ingestion boundary")
    print("=" * 74)

    print(f"\n  history_cutoff()         {cutoff}")
    print(f"  HISTORY_MONTHS           {get_settings().history_months}"
          "   (the mapper wrote the cutoff; this only reads it)")
    print(f"\n  event_log rows now       {total:>10,}")
    print(f"  work_item rows now       {cases:>10,}")
    print(f"  raw_payload rows now     {raw:>10,}   (never touched by this script)")

    by_activity = rows(conn, BY_ACTIVITY)
    candidates = sum(int(r[1]) for r in by_activity)

    print("\n  WOULD DELETE - events timestamped before the cutoff")
    print(f"    {'activity':<20} {'rows':>7}  {'oldest':<26} {'newest':<26}")
    print(f"    {'-' * 20} {'-' * 7}  {'-' * 26} {'-' * 26}")
    for activity, n, oldest, newest in by_activity:
        print(f"    {activity:<20} {int(n):>7,}  {oldest!s:<26} {newest!s:<26}")
    print(f"    {'-' * 20} {'-' * 7}")
    print(f"    {'TOTAL':<20} {candidates:>7,}")

    commits = scalar(conn, COMMITS_BELOW_CUTOFF)
    print("\n  WOULD KEEP")
    print(f"    {'events at or after the cutoff':<44} {total - candidates:>8,}")
    print(f"    {'commit events below the cutoff':<44} {commits:>8,}"
          f"   {'(none, as expected)' if commits == 0 else '<-- REFUSES BELOW'}")

    low, high, surviving = (
        rows(conn, SURVIVING_WINDOW)[0] if candidates else (None, None, total)
    )
    if candidates:
        print("\n  event window after the purge")
        print(f"    {low!s}  ..  {high!s}")
        print(f"    {surviving:,} events")

    would_orphan = scalar(conn, WOULD_ORPHAN)
    already = scalar(conn, ALREADY_EMPTY)
    print("\n  ORPHANED CASES - reported, NOT deleted")
    print(f"    {'cases that would be left with no events':<44} {would_orphan:>8,}")
    print(f"    {'cases that already have no events':<44} {already:>8,}")
    if would_orphan:
        for source, n in rows(conn, WOULD_ORPHAN_BY_SOURCE):
            print(f"      case_source = {source!s:<28} {int(n):>8,}")
    print("    A case with no events is real data - a Jira ticket that never")
    print("    produced code, an open PR nobody reviewed. Deleting cases is a")
    print("    separate decision and this script does not make it.")

    print("\n  REVERSIBLE")
    print("    raw_payload still holds every payload behind these rows.")
    print("    python -m app.normalise.event_log --no-window-filter")
    print("    maps them back with no re-fetch.")
    return candidates


def guard(conn: Connection, candidates: int) -> str | None:
    """Reasons to refuse. Returns a message, or None when it is safe."""
    if candidates == 0:
        return "nothing to delete - no event sits below the cutoff"

    cutoff = conn.execute(text("SELECT history_cutoff()")).scalar_one()
    if scalar(conn, "SELECT (history_cutoff() = '-infinity'::timestamptz)::int"):
        return (
            "history_cutoff() is -infinity, meaning run_config has no "
            "'history_cutoff' row. Run `python -m app.normalise.event_log` "
            "first - record_cutoff() writes it."
        )
    if scalar(conn, "SELECT (history_cutoff() > now())::int"):
        return (
            f"history_cutoff() is {cutoff}, which is in the future. Every "
            "event in the log is below it and this would empty the table."
        )

    commits = scalar(conn, COMMITS_BELOW_CUTOFF)
    if commits:
        return (
            f"{commits} commit event(s) sit below the cutoff. git_local "
            "re-applies the window to the author date, so this should be "
            "impossible - and a commit event is the one thing here that "
            "raw_payload alone cannot rebuild, since re-mapping it needs the "
            "git clone. Investigate before deleting anything."
        )

    blocked = scalar(conn, BLOCKING_FK)
    if blocked:
        return (
            f"{blocked} candidate row(s) are referenced by cost_event via "
            "cost_event_event_id_fkey. Deleting them would break that FK."
        )
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument(
        "--execute",
        action="store_true",
        help="actually delete. Without this the script only reports.",
    )
    args = parser.parse_args(argv)

    engine = create_engine(get_settings().database_url, future=True, pool_pre_ping=True)
    try:
        # Reporting finishes and closes its connection before any write starts.
        # engine.connect() autobegins on the first execute(), so conn.begin()
        # afterwards would raise; the delete uses engine.begin(), which owns
        # its transaction from the start.
        with engine.connect() as conn:
            candidates = render_plan(conn)
            refusal = guard(conn, candidates)

        if not args.execute:
            print("\n" + "=" * 74)
            print("  DRY RUN - nothing was deleted.")
            if refusal:
                print(f"  --execute would REFUSE: {refusal}")
            else:
                print("  Re-run with --execute to delete the rows listed above.")
            print("=" * 74)
            return 0

        if refusal:
            print(f"\nREFUSING TO EXECUTE: {refusal}", file=sys.stderr)
            return 1

        # Re-check and delete in ONE transaction. The plan above was read in a
        # different transaction and this database has several people writing to
        # it; a guard that passed a second ago is not a guarantee at the moment
        # of the DELETE. Raising in here rolls the whole thing back.
        with engine.begin() as conn:
            recheck = guard(conn, scalar(conn, COUNT_CANDIDATES))
            if recheck:
                raise Refused(recheck)

            raw_before = scalar(conn, "SELECT count(*) FROM raw_payload")
            before = scalar(conn, "SELECT count(*) FROM event_log")
            deleted = conn.execute(text(DELETE_SQL)).rowcount
            raw_after = scalar(conn, "SELECT count(*) FROM raw_payload")
            if raw_before != raw_after:
                raise Refused(
                    f"raw_payload moved {raw_before:,} -> {raw_after:,} inside "
                    "the delete transaction. Rolled back; nothing was deleted."
                )

            after = scalar(conn, "SELECT count(*) FROM event_log")
            low, high, _ = rows(conn, "SELECT min(ts), max(ts), count(*) FROM event_log")[0]
            orphans = scalar(conn, ALREADY_EMPTY)

        print("\n" + "=" * 74)
        print(f"  deleted                  {deleted:>10,}")
        print(f"  event_log  {before:,} -> {after:,}")
        print(f"  raw_payload {raw_before:,} -> {raw_after:,}   unchanged")
        print(f"  event window now  {low}  ..  {high}")
        print(f"  cases with no events     {orphans:>10,}   (kept, never deleted)")
        print("=" * 74)
        print("  Re-run the mapper to refresh the sprint grid:")
        print("    python -m app.normalise.event_log")
    except Refused as exc:
        print(f"\nROLLED BACK: {exc}", file=sys.stderr)
        return 1
    except SQLAlchemyError as exc:
        print(f"FATAL: {type(exc).__name__}: {exc}", file=sys.stderr)
        if "history_cutoff" in str(exc):
            print(
                "  history_cutoff() is created by migrations/"
                "002_canonical_event_log.sql, which the mapper applies on every "
                "run. Run `python -m app.normalise.event_log` first.",
                file=sys.stderr,
            )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
