#!/usr/bin/env python3
"""
Ingestion contract check — the gate between "rows exist" and "rows are usable".
Owner: Dipen (normalise + models lane).
Phase: Tier 0. Nobody builds a view on top of this data until it passes.

    python scripts/validate_ingest.py

Eleven checks. Exit codes:

    0  every check passed (warnings are allowed)
    1  at least one check FAILED — do not build on this data
    2  nothing failed, but some checks could not run

Check 11 is the only one that can never fail. It reports a property of the
data — cases whose span is an order of magnitude past the median, which on
Apache repos means an umbrella ticket — not a defect in the pipeline. Blocking
a build on it would block it on something no code change fixes.

WHY THIS USES THE WRITE ROLE
----------------------------
The obvious instinct is to open the read-only engine from `app.db.session`.
It does not work, by design: `esi_app` is granted SELECT on views only, and
`backend/tests/test_db_session.py` asserts that `SELECT count(*) FROM
event_log` raises "permission denied" for that role. Checks 1, 2, 3, 5, 6 and
8 read base tables, so the app role physically cannot run them.

So this script connects with the WRITE role but opens every connection with
`default_transaction_read_only = on`, the same server-enforced guard the read
engine uses. It gets the grants of a validator and the powers of a reader:
Postgres rejects an INSERT from this script before privileges are consulted.

`--engine app` opens the real read-only engine instead and runs only the
checks the view surface can answer, reporting the rest as SKIP. That mode
exists to prove what the API can see, not to validate an ingest.

WHAT IS A JUDGEMENT CALL HERE
-----------------------------
Four thresholds are ours, not the schema's. Each is printed in the output next
to the number it judges, so nobody has to read this file to know what "enough"
meant:

    MIN_ITEMS_PER_SPRINT        what makes a sprint window trainable
    MIN_SPRINTS_FOR_TRAINING    how many such windows the forecaster needs
    MIN_REVIEW_LATENCY_COVERAGE what fraction of work items must have one
    UMBRELLA_SPAN_MULTIPLE      how far past the median span is "a programme"

The last one lives in `app.normalise.case_span`, not here, because the mapper's
run report applies the same rule and the two must not drift. `--span-multiple`
overrides it for one run. It is not a Settings field: `app/config.py` is
Nishant's, and one field per key in .env.example is his discipline to change.

The vocabulary in check 4 and the source enum in check 5 are NOT judgement
calls: both are parsed out of docs/schema.sql at run time rather than copied
into this file, so a drift between the frozen contract and the live database
surfaces here instead of on stage.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.config import get_settings
from app.db.session import READ_ONLY_CONNECT_ARGS, get_read_engine
from app.ingestion.pseudonymize import IdentityLeak, assert_no_identity
from app.normalise.case_span import UMBRELLA_SPAN_MULTIPLE, CaseSpan, summarise
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError

SCHEMA_PATH = REPO_ROOT / "docs" / "schema.sql"

# --- thresholds we chose (see module docstring) --------------------------

#: A sprint window with fewer work items than this teaches the forecaster
#: noise. 14-day windows over a 12-month history give ~26 windows, so this is
#: deliberately low enough to keep most of them.
MIN_ITEMS_PER_SPRINT = 10

#: Below this many trainable windows a time-based split has nothing to hold
#: out. A random split would leak the future, so more windows is the only fix.
MIN_SPRINTS_FOR_TRAINING = 8

#: Review latency is the headline waste metric. Under this coverage the
#: WasteView is describing a minority of the work and should say so.
MIN_REVIEW_LATENCY_COVERAGE = 0.40

#: How many offending ids to print before truncating. Check 2 is exempt: if
#: the commit-to-PR mapping is broken, the full list IS the deliverable.
SAMPLE_LIMIT = 20

#: Check 11 prints the worst offenders, not all of them - the count is the
#: finding, the list is the evidence.
UMBRELLA_TOP_N = 10

# --- row-count expectations ----------------------------------------------
# `required` means an empty table is a FAIL: without it there is no product.
# Everything else is reported but not judged, because Tier 1 and Tier 2 have
# not landed yet and an empty cost_event at hour 6 is correct, not broken.


@dataclass(frozen=True)
class TableExpectation:
    required: bool
    max_plausible: int | None
    note: str


# Printed strings stay ASCII-only: the team is on Windows terminals whose
# default code page turns an em dash into a replacement character.
TABLE_EXPECTATIONS: dict[str, TableExpectation] = {
    "run_config": TableExpectation(True, 200, "config knobs, one row per key"),
    "raw_payload": TableExpectation(True, 5_000_000, "land raw, then map"),
    "actor": TableExpectation(True, 100_000, "pseudonymous contributors"),
    "work_item": TableExpectation(True, 1_000_000, "the case, whatever its source"),
    "event_log": TableExpectation(True, 20_000_000, "everything hangs off this"),
    "ingest_cursor": TableExpectation(False, 1_000, "paging bookmarks"),
    "rate_card": TableExpectation(False, 50, "Tier 1 - Diljit"),
    "work_session": TableExpectation(False, 10_000_000, "Tier 1 - session inference"),
    "cost_event": TableExpectation(False, 20_000_000, "Tier 1 - cost attribution"),
    "ci_run": TableExpectation(False, 5_000_000, "Tier 1 - CI waste"),
    "calendar_event": TableExpectation(False, 5_000_000, "Tier 1 - synthetic"),
    "ai_usage": TableExpectation(False, 5_000_000, "Tier 2 - synthetic, never per-person"),
    "variant": TableExpectation(False, 1_000_000, "Tier 0 - variant graph"),
    "code_churn": TableExpectation(False, 5_000_000, "Tier 1 - rework"),
    "backtest_result": TableExpectation(False, 100_000, "Tier 2 - forecaster accuracy"),
}

PASS, FAIL, WARN, SKIP = "PASS", "FAIL", "WARN", "SKIP"


@dataclass(frozen=True)
class CheckResult:
    """One check's verdict. `detail` and `table` are printed under the summary."""

    number: int
    name: str
    status: str
    headline: str
    detail: tuple[str, ...] = ()
    table: tuple[Sequence[object], ...] = ()
    columns: tuple[str, ...] = ()
    banner: bool = False


@dataclass
class Probe:
    """Every SQL statement the script issues, in one place.

    Split from the evaluators deliberately: the evaluators are pure functions
    over plain rows, so the interesting logic is unit-testable with a fixture
    dataset and no database at all. See backend/tests/test_validate_ingest.py.
    """

    conn: Connection

    def scalar(self, sql: str, **params: object) -> object:
        return self.conn.execute(text(sql), params).scalar_one()

    def rows(self, sql: str, **params: object) -> list[tuple]:
        return [tuple(r) for r in self.conn.execute(text(sql), params).all()]

    # -- check 1
    def row_counts(self) -> dict[str, int]:
        tables = [
            str(r[0])
            for r in self.rows(
                "SELECT c.relname FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relkind = 'r' "
                "ORDER BY c.relname"
            )
        ]
        # Identifiers cannot be bound as parameters; relname came from
        # pg_catalog, not from user input, and is quoted on the way in.
        return {
            table: int(self.scalar(f'SELECT count(*) FROM "{table}"'))
            for table in tables
        }

    def event_window(self) -> tuple[object, object]:
        rows = self.rows("SELECT min(ts), max(ts) FROM event_log")
        return rows[0] if rows else (None, None)

    # -- check 2
    def merge_order_offenders(self) -> list[tuple]:
        return self.rows(
            """
            SELECT work_item_id, first_merged, first_commit,
                   CASE WHEN first_commit IS NULL
                        THEN 'merged_with_no_commit'
                        ELSE 'merged_before_first_commit' END AS kind
            FROM (
                SELECT work_item_id,
                       min(ts) FILTER (WHERE activity = 'merged') AS first_merged,
                       min(ts) FILTER (WHERE activity = 'commit') AS first_commit
                FROM event_log
                GROUP BY work_item_id
            ) t
            WHERE first_merged IS NOT NULL
              AND (first_commit IS NULL OR first_merged < first_commit)
            ORDER BY work_item_id
            """
        )

    # -- check 3
    def orphan_actor_hashes(self) -> list[str]:
        return [
            str(r[0])
            for r in self.rows(
                "SELECT DISTINCT e.actor_hash FROM event_log e "
                "LEFT JOIN actor a ON a.actor_hash = e.actor_hash "
                "WHERE e.actor_hash IS NOT NULL AND a.actor_hash IS NULL "
                "ORDER BY 1"
            )
        ]

    # -- check 4
    def activity_counts(self, relation: str) -> dict[str, int]:
        return {
            str(r[0]): int(r[1])
            for r in self.rows(
                f"SELECT activity, count(*) FROM {relation} "
                "GROUP BY 1 ORDER BY 2 DESC"
            )
        }

    # -- check 5
    def source_counts(self, relation: str) -> dict[object, int]:
        return {
            r[0]: int(r[1])
            for r in self.rows(
                f"SELECT source, count(*) FROM {relation} "
                "GROUP BY 1 ORDER BY 2 DESC"
            )
        }

    # -- check 6
    def sprintless_work_items(self) -> list[str]:
        return [
            str(r[0])
            for r in self.rows(
                "SELECT work_item_id FROM work_item WHERE sprint IS NULL ORDER BY 1"
            )
        ]

    # -- check 7
    def public_columns(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for relation, column in self.rows(
            "SELECT c.relname, a.attname "
            "FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "JOIN pg_attribute a ON a.attrelid = c.oid "
            "WHERE n.nspname = 'public' "
            "  AND c.relkind IN ('r', 'v', 'm', 'p', 'f') "
            "  AND a.attnum > 0 AND NOT a.attisdropped "
            "ORDER BY 1, 2"
        ):
            out.setdefault(str(relation), []).append(str(column))
        return out

    # -- check 8
    def actors_per_component(self) -> list[tuple]:
        return self.rows(
            """
            SELECT w.component, count(DISTINCT e.actor_hash) AS n_actors,
                   count(DISTINCT w.work_item_id) AS n_items, count(*) AS n_events
            FROM work_item w
            JOIN event_log e ON e.work_item_id = w.work_item_id
            WHERE e.actor_hash IS NOT NULL
            GROUP BY w.component
            ORDER BY n_actors DESC, w.component
            """
        )

    # -- check 9
    def review_latency_coverage(self) -> tuple[int, int]:
        covered = int(
            self.scalar("SELECT count(DISTINCT work_item_id) FROM v_review_latency")
        )
        total = int(self.scalar("SELECT count(*) FROM v_case_sequence"))
        return covered, total

    # -- check 10
    def items_per_sprint(self) -> list[tuple]:
        return self.rows(
            "SELECT sprint, count(*) AS n_items FROM v_case_sequence "
            "WHERE sprint IS NOT NULL GROUP BY sprint ORDER BY sprint"
        )

    # -- check 11
    def case_spans(self) -> list[tuple]:
        """Every case, with its span in days or null when it has not closed.

        Open cases are returned rather than filtered out: they are part of the
        denominator ("173 of 2,562"), and dropping them here would make the
        share of umbrella cases look larger than it is.
        """
        return self.rows(
            """
            SELECT work_item_id, case_source,
                   extract(epoch FROM (closed_at - opened_at)) / 86400.0 AS days
            FROM work_item
            ORDER BY work_item_id
            """
        )


# =========================================================================
# Contract parsed from docs/schema.sql - not copied into this file
# =========================================================================


def parse_check_vocabulary(sql_text: str, column: str) -> frozenset[str]:
    """Pull the allowed values out of `CHECK (<column> IN ('a','b',...))`.

    Reading the frozen contract at run time rather than duplicating it means a
    live database whose CHECK has drifted from docs/schema.sql is caught here.
    A hardcoded copy would agree with itself forever.
    """
    match = re.search(
        rf"CHECK\s*\(\s*{re.escape(column)}\s+IN\s*\((.*?)\)\s*\)",
        sql_text,
        re.IGNORECASE | re.DOTALL,
    )
    if match is None:
        raise ValueError(
            f"no CHECK ({column} IN (...)) found in docs/schema.sql - the "
            "contract moved and this script cannot validate against it"
        )
    return frozenset(re.findall(r"'([^']+)'", match.group(1)))


# =========================================================================
# Evaluators - pure functions over plain rows. No database, no I/O.
# =========================================================================


def evaluate_row_counts(
    counts: dict[str, int], window: tuple[object, object] = (None, None)
) -> CheckResult:
    missing = [
        table
        for table, exp in TABLE_EXPECTATIONS.items()
        if exp.required and counts.get(table, 0) == 0
    ]
    absent = [table for table in TABLE_EXPECTATIONS if table not in counts]
    implausible = [
        f"{table}={counts[table]:,} exceeds the plausible ceiling "
        f"{exp.max_plausible:,} - suspect duplicate ingestion"
        for table, exp in TABLE_EXPECTATIONS.items()
        if exp.max_plausible is not None and counts.get(table, 0) > exp.max_plausible
    ]

    # An event log smaller than the work-item table means cases with no
    # events, which breaks every sequence view downstream.
    events, items = counts.get("event_log", 0), counts.get("work_item", 0)
    if items and events < items:
        implausible.append(
            f"event_log ({events:,}) < work_item ({items:,}) - some cases have "
            "no events at all and will vanish from every sequence view"
        )

    rows = [
        (
            table,
            f"{count:,}",
            "required" if TABLE_EXPECTATIONS.get(table, _UNKNOWN).required else "",
            TABLE_EXPECTATIONS.get(table, _UNKNOWN).note,
        )
        for table, count in sorted(counts.items())
    ]

    detail: list[str] = []
    if items:
        detail.append(f"events per work item: {events / items:.1f}")
    if window[0] is not None:
        detail.append(f"event window: {window[0]} .. {window[1]}")
    for line in implausible:
        detail.append(f"IMPLAUSIBLE: {line}")
    for table in absent:
        detail.append(f"table {table!r} is in the schema but not in the database")

    if missing or absent:
        status, headline = FAIL, (
            f"required tables empty or absent: {', '.join(sorted(missing + absent))}"
        )
    elif implausible:
        status, headline = FAIL, f"{len(implausible)} implausible row count(s)"
    else:
        status, headline = PASS, (
            f"{len(counts)} tables, all required ones non-zero and plausible"
        )

    return CheckResult(
        1,
        "Row counts per table",
        status,
        headline,
        tuple(detail),
        tuple(rows),
        ("table", "rows", "req?", "note"),
    )


_UNKNOWN = TableExpectation(False, None, "not in the frozen schema")


def evaluate_merge_order(offenders: Sequence[tuple], n_merged: int) -> CheckResult:
    """Check 2 - the one that invalidates everything downstream if it fails.

    Two shapes of breakage, reported separately because they have different
    causes: a merged event genuinely earlier than the first commit (timestamps
    or case mapping wrong), and a merged event on a case with no commit at all
    (the commit-to-PR join found nothing).

    `n_merged` exists to stop this check going green for the wrong reason. With
    no merged events in the log the condition is vacuously true, and a green
    row here would be read as "the commit-to-PR mapping is verified" when what
    it actually means is "there is nothing to verify yet".
    """
    before = [row for row in offenders if row[3] == "merged_before_first_commit"]
    no_commit = [row for row in offenders if row[3] == "merged_with_no_commit"]

    if n_merged == 0:
        return CheckResult(
            2,
            "No 'merged' before first 'commit'",
            WARN,
            "VACUOUS: event_log holds no 'merged' events, so there is nothing "
            "to order",
            (
                (
                    "This check cannot verify the commit-to-PR mapping until "
                    "the GitHub PR mapper writes 'merged' events."
                ),
                "Treat it as unproven, not as passed.",
            ),
        )

    if not offenders:
        return CheckResult(
            2,
            "No 'merged' before first 'commit'",
            PASS,
            f"{n_merged:,} merged events, all ordered after their first commit",
        )

    detail = [
        f"{len(before)} work item(s) merged BEFORE their first commit",
        f"{len(no_commit)} work item(s) merged with NO commit mapped at all",
        "",
        "The commit-to-PR mapping is broken. Cycle time, rework and every cost",
        "figure derived from them are wrong until this is fixed. Full list:",
        "",
    ]
    # No truncation here: the ids ARE the deliverable for this check.
    detail.extend(
        f"  {row[0]}  merged={row[1]}  first_commit={row[2]}  ({row[3]})"
        for row in offenders
    )
    return CheckResult(
        2,
        "No 'merged' before first 'commit'",
        FAIL,
        f"{len(offenders)} work item(s) with a broken commit-to-PR mapping",
        tuple(detail),
    )


def evaluate_orphan_actors(orphans: Sequence[str]) -> CheckResult:
    if not orphans:
        return CheckResult(
            3,
            "event_log.actor_hash resolves",
            PASS,
            "every non-null actor_hash exists in actor",
        )
    return CheckResult(
        3,
        "event_log.actor_hash resolves",
        FAIL,
        f"{len(orphans)} actor_hash value(s) not present in actor",
        tuple(_sample(orphans)),
    )


def evaluate_activity_vocabulary(
    counts: dict[str, int], allowed: Iterable[str]
) -> CheckResult:
    allowed = frozenset(allowed)
    unknown = sorted(set(counts) - allowed)
    unused = sorted(allowed - set(counts))
    rows = tuple((activity, f"{n:,}") for activity, n in counts.items())

    detail = [f"vocabulary from docs/schema.sql: {len(allowed)} activities"]
    if unused:
        detail.append(f"never observed (not an error): {', '.join(unused)}")
    if unknown:
        detail.append(
            f"OUTSIDE the schema CHECK: {', '.join(repr(a) for a in unknown)}"
        )
        return CheckResult(
            4,
            "activity in schema vocabulary",
            FAIL,
            f"{len(unknown)} activity value(s) outside the CHECK constraint",
            tuple(detail),
            rows,
            ("activity", "events"),
        )
    return CheckResult(
        4,
        "activity in schema vocabulary",
        PASS,
        f"{len(counts)} distinct activities, all inside the CHECK constraint",
        tuple(detail),
        rows,
        ("activity", "events"),
    )


def evaluate_source(counts: dict[object, int], allowed: Iterable[str]) -> CheckResult:
    """Decision #11: a row without a source is a bug. Bad values are too."""
    allowed = frozenset(allowed)
    nulls = counts.get(None, 0)
    unknown = sorted(
        str(s) for s in counts if s is not None and str(s) not in allowed
    )
    rows = tuple(
        ("(null)" if s is None else str(s), f"{n:,}") for s, n in counts.items()
    )

    problems = []
    if nulls:
        problems.append(f"{nulls:,} row(s) with a null source")
    if unknown:
        problems.append(f"source values outside the enum: {', '.join(unknown)}")
    if problems:
        return CheckResult(
            5,
            "event_log.source is populated",
            FAIL,
            "; ".join(problems),
            (
                (
                    "source drives the observed-vs-modelled badge in the UI; "
                    "a row without it renders as neither."
                ),
            ),
            rows,
            ("source", "events"),
        )
    return CheckResult(
        5,
        "event_log.source is populated",
        PASS,
        f"every row has a source, all within {sorted(allowed)}",
        (),
        rows,
        ("source", "events"),
    )


def evaluate_sprints_present(sprintless: Sequence[str], total: int) -> CheckResult:
    if not sprintless:
        return CheckResult(
            6,
            "work_item.sprint is populated",
            PASS,
            f"all {total:,} work items carry a sprint",
        )
    return CheckResult(
        6,
        "work_item.sprint is populated",
        FAIL,
        f"{len(sprintless):,} of {total:,} work items have a null sprint",
        (
            (
                "Sprint is derived once at ingestion so every lane splits "
                "identically. A null sprint drops it from every time split."
            ),
            *_sample(sprintless),
        ),
    )


def evaluate_no_identity_columns(columns: dict[str, list[str]]) -> CheckResult:
    """Check 7 - the rule itself comes from app.ingestion.pseudonymize.

    `assert_no_identity` is called once per relation rather than over one
    flattened list so the failure message names the relation that leaks. The
    token list lives in pseudonymize.py and is not restated here; that module
    is the single definition of what counts as identity.
    """
    leaks: list[str] = []
    for relation, names in sorted(columns.items()):
        try:
            assert_no_identity(names)
        except IdentityLeak as exc:
            leaks.append(f"{relation}: {exc}")

    n_columns = sum(len(v) for v in columns.values())
    if leaks:
        return CheckResult(
            7,
            "No identity columns anywhere",
            FAIL,
            f"identity-shaped columns in {len(leaks)} relation(s)",
            tuple(leaks),
        )
    return CheckResult(
        7,
        "No identity columns anywhere",
        PASS,
        f"{n_columns} columns across {len(columns)} relations, none identity-shaped",
    )


def evaluate_k_anon_survival(
    rows: Sequence[tuple], floor: int, fallback: int
) -> CheckResult:
    """Check 8 - the number the team needs most. Rendered as a banner.

    Verdict rule, printed alongside the numbers so it is not a mystery:
      PASS  at least one component clears the floor
      WARN  none clear the floor but some clear the fallback
      FAIL  nothing clears even the fallback - every view suppresses
    """
    survivors_floor = [r for r in rows if int(r[1]) >= floor]
    survivors_fallback = [r for r in rows if int(r[1]) >= fallback]

    table = tuple(
        (
            "(none)" if r[0] is None else str(r[0]),
            int(r[1]),
            f"{int(r[2]):,}",
            f"{int(r[3]):,}",
            "yes" if int(r[1]) >= floor else ("fallback" if int(r[1]) >= fallback else "SUPPRESSED"),
        )
        for r in rows
    )

    headline = (
        f"{len(survivors_floor)}/{len(rows)} components survive k={floor}  |  "
        f"{len(survivors_fallback)}/{len(rows)} survive k={fallback}"
    )
    detail = (
        f"K_ANONYMITY_FLOOR    = {floor}   -> {len(survivors_floor)} component(s) render",
        f"K_ANONYMITY_FALLBACK = {fallback}   -> {len(survivors_fallback)} component(s) render",
        f"components suppressed entirely: {len(rows) - len(survivors_fallback)}",
        "",
        "Distinct actors per component over the whole event window. If the top",
        "number is small, SpendView is mostly blank and the demo needs the",
        "fallback - which must be printed on screen when it is used.",
    )

    if survivors_floor:
        status = PASS
    elif survivors_fallback:
        status = WARN
    else:
        status = FAIL

    return CheckResult(
        8,
        "K-ANON SURVIVAL",
        status,
        headline,
        detail,
        table,
        ("component", "n_actors", "work_items", "events", f"survives k={floor}?"),
        banner=True,
    )


def evaluate_review_latency(covered: int, total: int) -> CheckResult:
    coverage = covered / total if total else 0.0
    headline = (
        f"{covered:,}/{total:,} work items have a computable review latency "
        f"({coverage:.1%}, need {MIN_REVIEW_LATENCY_COVERAGE:.0%})"
    )
    if total == 0:
        return CheckResult(
            9, "Review latency coverage", FAIL, "no work items with events at all"
        )
    if coverage >= MIN_REVIEW_LATENCY_COVERAGE:
        return CheckResult(9, "Review latency coverage", PASS, headline)
    return CheckResult(
        9,
        "Review latency coverage",
        FAIL,
        headline,
        (
            (
                "v_review_latency needs a review_requested followed by a "
                "review, approved or changes_requested on the same case."
            ),
            (
                "Low coverage usually means review_requested events were not "
                "ingested, not that reviews did not happen."
            ),
        ),
    )


def evaluate_sprint_windows(rows: Sequence[tuple]) -> CheckResult:
    trainable = [r for r in rows if int(r[1]) >= MIN_ITEMS_PER_SPRINT]
    table = tuple(
        (
            int(r[0]),
            f"{int(r[1]):,}",
            "trainable" if int(r[1]) >= MIN_ITEMS_PER_SPRINT else "too thin",
        )
        for r in rows
    )
    headline = (
        f"{len(trainable)}/{len(rows)} sprint windows carry "
        f">= {MIN_ITEMS_PER_SPRINT} work items (need {MIN_SPRINTS_FOR_TRAINING})"
    )
    status = PASS if len(trainable) >= MIN_SPRINTS_FOR_TRAINING else FAIL
    detail = (
        (
            f"thresholds: MIN_ITEMS_PER_SPRINT={MIN_ITEMS_PER_SPRINT}, "
            f"MIN_SPRINTS_FOR_TRAINING={MIN_SPRINTS_FOR_TRAINING}"
        ),
        (
            "Time-based splits only - a random split leaks the future, so the "
            "forecaster needs enough windows to hold the last ones out."
        ),
    )
    return CheckResult(
        10,
        "Trainable sprint windows",
        status,
        headline,
        detail,
        table,
        ("sprint", "work_items", ""),
    )


def evaluate_case_spans(
    rows: Sequence[tuple], multiple: float = UMBRELLA_SPAN_MULTIPLE
) -> CheckResult:
    """Check 11 - the umbrella guard. WARN at worst, never FAIL.

    This is a property of open-source data, not a pipeline bug. Decision #6
    says a shared ticket key is one case, and Apache files main PRs, follow-ups
    and backports under one key - so `KAFKA-14133` is one case spanning 17
    months. The mapping is doing exactly what it was told to. Failing the
    ingest over it would block a build on something no code change can fix.

    What it IS is a warning to whoever reads cycle time next: a handful of
    cases are work programmes, and they sit in the same distribution as
    everything else. The rule lives in app.normalise.case_span so this check
    and the mapper's run report cannot drift apart.

    Only the span half of the rule fires here. `work_item` keeps one
    `source_ref` and no list of PR numbers, so this check cannot know how many
    PRs a case holds; the mapper can, and reports both halves.
    """
    cases = [
        CaseSpan(
            work_item_id=str(r[0]),
            days=None if r[2] is None else float(r[2]),
            n_prs=None,
            case_source=None if r[1] is None else str(r[1]),
        )
        for r in rows
    ]
    report = summarise(cases, multiple=multiple)
    name = "Umbrella case spans"

    if not cases:
        return CheckResult(
            11, name, WARN, "VACUOUS: work_item is empty, so there is no span "
            "distribution to judge"
        )
    if report.unscalable:
        return CheckResult(
            11,
            name,
            WARN,
            (
                "VACUOUS: no case has both an opened_at and a closed_at, so "
                "there is no median to scale"
            ),
            (
                (
                    f"{report.n_cases:,} case(s), {report.n_measurable} with a "
                    "measurable span."
                ),
                (
                    "Before the PR mapper runs, cases are provisional and carry "
                    "an opened_at only. Treat this as unproven, not as passed."
                ),
            ),
        )

    detail = [
        (
            f"rule: span > {multiple:g}x the median case span "
            f"(UMBRELLA_SPAN_MULTIPLE, --span-multiple)"
        ),
        (
            f"median = {report.median_days:.2f} days over {report.n_measurable:,} "
            f"closed case(s)  ->  threshold {report.threshold_days:.1f} days"
        ),
        "",
        "Decision #6 stands: a shared ticket key is one case, and that is not",
        "changed by this check. These cases are real, and merging them was",
        "correct - but their cycle time is a work-programme duration, so any",
        "average that includes them is describing two different things at once.",
    ]

    if not report.over_span:
        return CheckResult(
            11,
            name,
            PASS,
            f"no case exceeds {report.threshold_days:.1f} days "
            f"({multiple:g}x the {report.median_days:.2f}-day median)",
            tuple(detail),
        )

    table = tuple(
        (
            case.work_item_id,
            f"{case.days:,.1f}",
            f"{case.days / report.median_days:,.0f}x",
            case.case_source or "",
        )
        for case in report.over_span[:UMBRELLA_TOP_N]
    )
    if len(report.over_span) > UMBRELLA_TOP_N:
        detail.append(
            f"showing the top {UMBRELLA_TOP_N} by span of "
            f"{len(report.over_span):,} flagged."
        )
    return CheckResult(
        11,
        name,
        WARN,
        (
            f"{len(report.over_span):,} of {report.n_cases:,} cases span more "
            f"than {report.threshold_days:.1f} days ({multiple:g}x median)"
        ),
        tuple(detail),
        table,
        ("work_item_id", "span_days", "vs median", "case_source"),
    )


def _sample(values: Sequence[str]) -> list[str]:
    shown = [f"  {v}" for v in values[:SAMPLE_LIMIT]]
    if len(values) > SAMPLE_LIMIT:
        shown.append(f"  ... and {len(values) - SAMPLE_LIMIT} more")
    return shown


# =========================================================================
# Runner
# =========================================================================


def run_checks(
    probe: Probe,
    schema_sql: str,
    *,
    app_engine: bool,
    span_multiple: float = UMBRELLA_SPAN_MULTIPLE,
) -> list[CheckResult]:
    settings = get_settings()
    results: list[CheckResult] = []

    def skipped(number: int, name: str) -> CheckResult:
        return CheckResult(
            number,
            name,
            SKIP,
            "needs a base table; the app role is denied it by design",
            ("Re-run without --engine app to check this.",),
        )

    # event_log and v_event_seq expose the same activity/source columns, so
    # checks 4 and 5 can run in either mode. The rest cannot.
    events = "v_event_seq" if app_engine else "event_log"

    if app_engine:
        results.append(skipped(1, "Row counts per table"))
        results.append(skipped(2, "No 'merged' before first 'commit'"))
        results.append(skipped(3, "event_log.actor_hash resolves"))
    else:
        results.append(evaluate_row_counts(probe.row_counts(), probe.event_window()))
        n_merged = int(
            probe.scalar("SELECT count(*) FROM event_log WHERE activity = 'merged'")
        )
        results.append(evaluate_merge_order(probe.merge_order_offenders(), n_merged))
        results.append(evaluate_orphan_actors(probe.orphan_actor_hashes()))

    results.append(
        evaluate_activity_vocabulary(
            probe.activity_counts(events), parse_check_vocabulary(schema_sql, "activity")
        )
    )
    results.append(
        evaluate_source(
            probe.source_counts(events), parse_check_vocabulary(schema_sql, "source")
        )
    )

    if app_engine:
        results.append(skipped(6, "work_item.sprint is populated"))
    else:
        sprintless = probe.sprintless_work_items()
        total = int(probe.scalar("SELECT count(*) FROM work_item"))
        results.append(evaluate_sprints_present(sprintless, total))

    results.append(evaluate_no_identity_columns(probe.public_columns()))

    if app_engine:
        results.append(skipped(8, "K-ANON SURVIVAL"))
    else:
        results.append(
            evaluate_k_anon_survival(
                probe.actors_per_component(),
                settings.k_anonymity_floor,
                settings.k_anonymity_fallback,
            )
        )

    results.append(evaluate_review_latency(*probe.review_latency_coverage()))
    results.append(evaluate_sprint_windows(probe.items_per_sprint()))

    if app_engine:
        results.append(skipped(11, "Umbrella case spans"))
    else:
        results.append(evaluate_case_spans(probe.case_spans(), span_multiple))
    return results


# =========================================================================
# Rendering
# =========================================================================

_WIDTH = 78


def render_table(columns: Sequence[str], rows: Sequence[Sequence[object]]) -> list[str]:
    if not rows:
        return ["    (no rows)"]
    header = list(columns) if columns else [""] * len(rows[0])
    widths = [len(str(h)) for h in header]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    out = []
    if columns:
        out.append("    " + "  ".join(str(h).ljust(widths[i]) for i, h in enumerate(header)))
        out.append("    " + "  ".join("-" * w for w in widths))
    out.extend(
        "    " + "  ".join(str(c).ljust(widths[i]) for i, c in enumerate(row))
        for row in rows
    )
    return out


def render(results: Sequence[CheckResult], engine_label: str) -> str:
    lines = [
        "=" * _WIDTH,
        "INGEST VALIDATION - Engineering Spend Intelligence",
        f"engine: {engine_label}",
        "=" * _WIDTH,
        "",
        f"  {'#':<3} {'CHECK':<34} {'STATUS':<7} RESULT",
        f"  {'-' * 3} {'-' * 34} {'-' * 7} {'-' * 28}",
    ]
    for r in results:
        lines.append(f"  {r.number:<3} {r.name[:34]:<34} {r.status:<7} {r.headline}")
    lines.append("")

    for r in results:
        if not (r.detail or r.table):
            continue
        if r.banner:
            lines += [
                "",
                "#" * _WIDTH,
                f"#  CHECK {r.number}: {r.name}  [{r.status}]",
                f"#  {r.headline}",
                "#" * _WIDTH,
            ]
        else:
            lines += ["", f"-- {r.number}. {r.name}  [{r.status}] " + "-" * 20]
        lines.extend(f"    {line}" if line else "" for line in r.detail)
        if r.table:
            lines.append("")
            lines.extend(render_table(r.columns, r.table))

    counts = {s: sum(1 for r in results if r.status == s) for s in (PASS, FAIL, WARN, SKIP)}
    lines += [
        "",
        "=" * _WIDTH,
        (
            f"  {counts[PASS]} passed, {counts[FAIL]} failed, "
            f"{counts[WARN]} warned, {counts[SKIP]} skipped, of {len(results)}"
        ),
    ]
    if counts[FAIL]:
        failed = ", ".join(str(r.number) for r in results if r.status == FAIL)
        lines.append(f"  FAILED CHECKS: {failed} - do not build views on this data.")
    elif counts[SKIP]:
        lines.append("  No failures, but some checks could not run.")
    else:
        lines.append("  Ingest is safe to build on.")
    lines.append("=" * _WIDTH)
    return "\n".join(lines)


def exit_code(results: Sequence[CheckResult]) -> int:
    if any(r.status == FAIL for r in results):
        return 1
    if any(r.status == SKIP for r in results):
        return 2
    return 0


def build_engine(app_engine: bool) -> tuple[Engine, str]:
    if app_engine:
        engine = get_read_engine()
        return engine, f"{engine.url.username} (app role, views only)"
    # Write role for the grants, server-enforced read-only for the safety.
    engine = create_engine(
        get_settings().database_url,
        pool_pre_ping=True,
        future=True,
        connect_args=READ_ONLY_CONNECT_ARGS,
    )
    return engine, f"{engine.url.username} (validator, read-only transactions)"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument(
        "--engine",
        choices=("validator", "app"),
        default="validator",
        help="validator: write role in read-only transactions, sees base tables "
        "(default). app: the esi_app read engine, view surface only - base-table "
        "checks report SKIP.",
    )
    parser.add_argument(
        "--span-multiple",
        type=float,
        default=UMBRELLA_SPAN_MULTIPLE,
        metavar="N",
        help="check 11: flag cases whose span exceeds N times the median case "
        f"span (default {UMBRELLA_SPAN_MULTIPLE:g}). Warns, never fails.",
    )
    args = parser.parse_args(argv)
    app_engine = args.engine == "app"
    if args.span_multiple <= 0:
        print("FATAL: --span-multiple must be positive.", file=sys.stderr)
        return 1

    if not SCHEMA_PATH.exists():
        print(f"FATAL: {SCHEMA_PATH} is missing - it is the contract.", file=sys.stderr)
        return 1
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")

    engine, label = build_engine(app_engine)
    try:
        with engine.connect() as conn:
            results = run_checks(
                Probe(conn),
                schema_sql,
                app_engine=app_engine,
                span_multiple=args.span_multiple,
            )
    except SQLAlchemyError as exc:
        print(
            f"FATAL: cannot query the database as {label}.\n  {type(exc).__name__}: {exc}\n"
            "  Start it with: docker compose -f infra/docker-compose.yml up -d postgres",
            file=sys.stderr,
        )
        return 1

    print(render(results, label))
    return exit_code(results)


if __name__ == "__main__":
    raise SystemExit(main())
