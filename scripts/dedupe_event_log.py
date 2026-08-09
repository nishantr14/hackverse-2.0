#!/usr/bin/env python3
"""
Remove map_github.py's surplus event_log rows after the two-mapper collision.
Owner: Dipen (normalise + models lane).

    python scripts/dedupe_event_log.py              # dry run, the default
    python scripts/dedupe_event_log.py --execute    # actually delete

WHY THERE ARE TWO ROWS FOR ONE FACT
-----------------------------------
`app/normalise/map_github.py` and `app/normalise/event_log.py` both map
`github_graphql` payloads into `event_log`, and both have run against this
database. They agree on what happened and disagree on how to name it:

    merged   sha256("github_graphql|pull_request|<repo>#<n>|merged|<iso>")
             -- IDENTICAL in both. Same id, so the second run upserted the
             first in place. There is one row, not two, and nothing here
             touches `merged`.

    review   map_github puts the reviewer in the ENTITY id:
             "<repo>#<n>:review:<hash>"
             event_log appends it as a sixth HASH part.
             Different strings, different ids, two rows for one review.

So the surplus is exactly the non-merged PR events map_github wrote. This
script deletes those and only those.

HOW A ROW IS IDENTIFIED
-----------------------
By the attrs signature, because it is the only thing that survives in the
database: map_github writes `pr` and `state`; event_log writes `ingest_source`
and `pr_number`; git_local writes `sha`. A row is a deletion candidate only
when it carries `pr` WITHOUT `ingest_source`, so a row written by the other
mapper can never be selected however the ids collide.

WHAT IS DELIBERATELY LEFT ALONE
-------------------------------
  * `activity = 'commit'`      git_local's, never map_github's
  * `activity = 'merged'`      one row already, see above
  * anything with no counterpart under the match key

The match key is (work_item_id, activity, ts, actor_hash), NULL-safe on the
actor. That is a stricter key than the PR number and it leaves 343 rows
behind; `--show-leftovers` explains them. Deleting those is a separate
decision and this script does not make it.

`raw_payload` IS NEVER READ AND NEVER WRITTEN by this script. It is the layer
that makes every re-run cheap, it has been wiped twice already, and a
verification below asserts its row count is unchanged.
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

#: A row this script may delete: written by map_github, not by event_log, not a
#: commit, not a merge, and with an equivalent row from the other mapper.
#: `IS NOT DISTINCT FROM` so two events that both legitimately have no actor
#: still match each other; plain `=` is NULL for that pair and would silently
#: protect every bot-authored and CI row from deduplication.
CANDIDATE_PREDICATE = """
    m.attrs ? 'pr'
AND NOT m.attrs ? 'ingest_source'
AND m.activity NOT IN ('commit', 'merged')
AND EXISTS (
        SELECT 1 FROM event_log o
         WHERE o.attrs ? 'ingest_source'
           AND o.work_item_id = m.work_item_id
           AND o.activity     = m.activity
           AND o.ts           = m.ts
           AND o.actor_hash IS NOT DISTINCT FROM m.actor_hash
    )
"""

BY_ACTIVITY = f"""
SELECT m.activity, count(*) AS n
  FROM event_log m
 WHERE {CANDIDATE_PREDICATE}
 GROUP BY 1 ORDER BY 2 DESC
"""

CANDIDATE_IDS = f"SELECT m.event_id FROM event_log m WHERE {CANDIDATE_PREDICATE}"

DELETE_SQL = f"DELETE FROM event_log m WHERE {CANDIDATE_PREDICATE}"

#: Rows map_github wrote that the match key does NOT pair up. Measured: 342 of
#: them are the same pull request filed under a DIFFERENT work_item_id by the
#: two mappers - event_log.py refuses a ticket key that appears only in a PR
#: body when git already built a `<repo>#<n>` case, and map_github does not.
#: They are duplicate facts on disagreeing case ids, not unique evidence, but
#: the requested match key does not catch them and this script will not guess.
LEFTOVERS = """
SELECT m.activity,
       count(*) AS unmatched,
       count(*) FILTER (
           WHERE EXISTS (
               SELECT 1 FROM event_log o
                WHERE o.attrs ? 'ingest_source'
                  AND o.attrs->>'pr_number' = split_part(m.attrs->>'pr', '#', 2)
                  AND o.activity = m.activity
                  AND o.ts = m.ts
                  AND o.actor_hash IS NOT DISTINCT FROM m.actor_hash)
       ) AS same_pr_other_case
  FROM event_log m
 WHERE m.attrs ? 'pr'
   AND NOT m.attrs ? 'ingest_source'
   AND m.activity NOT IN ('commit', 'merged')
   AND NOT EXISTS (
        SELECT 1 FROM event_log o
         WHERE o.attrs ? 'ingest_source'
           AND o.work_item_id = m.work_item_id
           AND o.activity     = m.activity
           AND o.ts           = m.ts
           AND o.actor_hash IS NOT DISTINCT FROM m.actor_hash)
 GROUP BY 1 ORDER BY 2 DESC
"""

#: A candidate referenced by cost_event cannot be deleted without breaking
#: cost_event_event_id_fkey. cost_event is empty today; this is here so that
#: stops being an assumption the moment Diljit's lane lands.
BLOCKING_FK = f"""
SELECT count(*) FROM cost_event c
 WHERE c.event_id IN ({CANDIDATE_IDS})
"""

WRITER_CENSUS = """
SELECT CASE
         WHEN attrs ? 'ingest_source' THEN 'event_log.py  (ingest_source, pr_number)'
         WHEN attrs ? 'pr'            THEN 'map_github.py (pr, state)'
         WHEN activity = 'commit'     THEN 'git_local     (sha)'
         ELSE 'unrecognised - investigate before deleting anything'
       END AS writer,
       count(*) AS n
  FROM event_log GROUP BY 1 ORDER BY 2 DESC
"""


class Refused(RuntimeError):
    """A safety check failed. Raised inside the write transaction to roll back."""


def census(conn: Connection) -> list[tuple]:
    return [tuple(r) for r in conn.execute(text(WRITER_CENSUS))]


def scalar(conn: Connection, sql: str) -> int:
    return int(conn.execute(text(sql)).scalar_one())


def rows(conn: Connection, sql: str) -> list[tuple]:
    return [tuple(r) for r in conn.execute(text(sql))]


def count_candidates(conn: Connection) -> int:
    return scalar(conn, f"SELECT count(*) FROM event_log m WHERE {CANDIDATE_PREDICATE}")


def render_plan(conn: Connection, show_leftovers: bool) -> tuple[int, int]:
    """Print what would be deleted. Returns (candidates, event_log total)."""
    total = scalar(conn, "SELECT count(*) FROM event_log")
    raw = scalar(conn, "SELECT count(*) FROM raw_payload")

    print("=" * 74)
    print("EVENT LOG DEDUPE - map_github.py surplus rows")
    print("=" * 74)

    print(f"\n  event_log rows now       {total:>10,}")
    print(f"  raw_payload rows now     {raw:>10,}   (never touched by this script)")

    print("\n  who wrote what")
    for writer, n in census(conn):
        print(f"    {writer:<44} {n:>8,}")

    by_activity = rows(conn, BY_ACTIVITY)
    candidates = sum(int(n) for _, n in by_activity)

    print("\n  WOULD DELETE - map_github rows with an event_log.py counterpart")
    print(f"    {'activity':<22} {'rows':>8}")
    print(f"    {'-' * 22} {'-' * 8}")
    for activity, n in by_activity:
        print(f"    {activity:<22} {int(n):>8,}")
    print(f"    {'-' * 22} {'-' * 8}")
    print(f"    {'TOTAL':<22} {candidates:>8,}")

    print("\n  WOULD KEEP")
    for label, sql in (
        ("commit events (git_local)",
         "SELECT count(*) FROM event_log WHERE activity = 'commit'"),
        ("merged events (upserted, one row already)",
         "SELECT count(*) FROM event_log WHERE activity = 'merged'"),
        ("event_log.py rows",
         "SELECT count(*) FROM event_log WHERE attrs ? 'ingest_source'"),
    ):
        print(f"    {label:<44} {scalar(conn, sql):>8,}")

    leftovers = rows(conn, LEFTOVERS)
    unmatched = sum(int(r[1]) for r in leftovers)
    same_pr = sum(int(r[2]) for r in leftovers)
    print(f"    {'map_github rows with no counterpart':<44} {unmatched:>8,}")

    if unmatched:
        print(f"\n  ABOUT THOSE {unmatched} UNMATCHED ROWS - read before you decide")
        print(f"    {same_pr} of them describe the SAME pull request as an")
        print("    event_log.py row, but the two mappers filed it under different")
        print("    work_item_ids, so the match key does not pair them. They are")
        print("    duplicate facts on disagreeing case ids, not unique evidence.")
        print("    They are NOT deleted here: the requested key does not select")
        print("    them, and merging two case ids is a different decision.")
        if show_leftovers:
            print(f"\n    {'activity':<22} {'unmatched':>10} {'same PR, other case':>21}")
            for activity, n, same in leftovers:
                print(f"    {activity:<22} {int(n):>10,} {int(same):>21,}")
        else:
            print("    Re-run with --show-leftovers for the breakdown.")

    print(f"\n  after deletion event_log would hold {total - candidates:,} rows")
    return candidates, total


def guard(conn: Connection, candidates: int) -> str | None:
    """Reasons to refuse. Returns a message, or None when it is safe."""
    if candidates == 0:
        return "nothing to delete - the candidate set is empty"
    unrecognised = [n for w, n in census(conn) if w.startswith("unrecognised")]
    if unrecognised:
        return (
            f"{sum(unrecognised)} row(s) match no known writer signature. "
            "Identify them before deleting anything."
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
    parser.add_argument(
        "--show-leftovers",
        action="store_true",
        help="break down the rows the match key does not pair up",
    )
    args = parser.parse_args(argv)

    engine = create_engine(get_settings().database_url, future=True, pool_pre_ping=True)
    try:
        # Reporting gets its own connection and is finished with before any
        # write starts. `engine.connect()` autobegins a transaction on the
        # first execute(), so calling conn.begin() afterwards raises - the
        # delete therefore uses engine.begin(), which owns its transaction
        # from the start rather than trying to nest inside an implicit one.
        with engine.connect() as conn:
            candidates, before = render_plan(conn, args.show_leftovers)
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
        # different transaction and this database has been written to by other
        # people mid-session; a guard that passed a second ago is not a
        # guarantee at the moment of the DELETE. Raising in here rolls back.
        with engine.begin() as conn:
            recheck = guard(conn, count_candidates(conn))
            if recheck:
                raise Refused(recheck)

            raw_before = scalar(conn, "SELECT count(*) FROM raw_payload")
            deleted = conn.execute(text(DELETE_SQL)).rowcount
            raw_after = scalar(conn, "SELECT count(*) FROM raw_payload")
            if raw_before != raw_after:
                raise Refused(
                    f"raw_payload moved {raw_before:,} -> {raw_after:,} inside "
                    "the delete transaction. Rolled back; nothing was deleted."
                )

            after = scalar(conn, "SELECT count(*) FROM event_log")
            remaining = scalar(
                conn,
                "SELECT count(*) FROM event_log "
                "WHERE attrs ? 'pr' AND NOT attrs ? 'ingest_source'",
            )

        print("\n" + "=" * 74)
        print(f"  deleted                  {deleted:>10,}")
        print(f"  event_log  {before:,} -> {after:,}")
        print(f"  raw_payload {raw_before:,} -> {raw_after:,}   unchanged")
        print(f"  map_github rows still present {remaining:>5,}  "
              "(the unmatched ones, kept on purpose)")
        print("=" * 74)
    except Refused as exc:
        print(f"\nROLLED BACK: {exc}", file=sys.stderr)
        return 1
    except SQLAlchemyError as exc:
        print(f"FATAL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
