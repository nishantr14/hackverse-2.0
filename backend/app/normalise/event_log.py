"""raw_payload -> work_item + actor + event_log. The integration layer.

Reads only from Postgres, writes only to Postgres, opens no sockets. Every
mapper is idempotent: event ids are deterministic hashes of their evidence, so
a second run upserts the same rows rather than doubling the log.

WHAT THIS MODULE DOES NOT DO
    No cost. No session inference. No ML. No simulation. It produces the
    event log the other three lanes read, and stops.

THE JOIN SPINE
    A case is a work item. Its id comes from one fallback chain, implemented
    once in `git_local.work_item_id_for` and mirrored here for PRs:

        Jira ticket key  ->  owner/name#pr_number  ->  owner/name@sha12

    A Jira key is globally unique across the ASF, so KAFKA-123 and FLINK-123
    cannot collide. Both fallbacks are repo-qualified, so apache/kafka#500 and
    apache/flink#500 cannot either. That is rule 6 satisfied by construction
    rather than by a repo column somebody remembers to filter on.

WHERE A JOIN CANNOT BE PROVEN
    `ci_run.work_item_id` stays NULL and the row survives — 36% of Actions runs
    are triggered by a merge ref that never appears as a commit in a shallow
    clone. `event_log.work_item_id` is NOT NULL in the frozen schema, so an
    unattributable CI run cannot become an event; it stays in `ci_run`, is
    counted in the verification report, and is not silently discarded.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Actor, EventLog, RawPayload, RunConfig, WorkItem
from app.db.session import write_session
from app.ingestion.git_local import PR_RE, assign_sprints, infer_tenure
from app.ingestion.projects import (
    PROJECT_TO_REPO,
    TICKET_ANYWHERE,
    epic_from_text,
    is_real_ticket,
)
from app.ingestion.pseudonymize import assert_no_identity
from app.normalise.activities import (
    JIRA_STATUS_TO_ACTIVITY,
    REVIEW_STATE_TO_ACTIVITY,
    TIMELINE_TO_ACTIVITY,
    assert_writable,
)

logger = logging.getLogger(__name__)


#: Days per month, as `github_connector` and `git_local` both compute it. The
#: three must agree exactly: a cutoff half a day from git_local's would sort
#: the same fortnight of commits and PRs into different windows.
DAYS_PER_MONTH = 30.44

#: Recorded in run_config so `in_window` in v_event_log is reproducible from
#: the database alone, without knowing what HISTORY_MONTHS was on the day.
CUTOFF_KEY = "history_cutoff"
CUTOFF_NOTE = (
    "events at or after this instant are in-window; earlier ones are retained "
    "because a case history cannot be reconstructed without them"
)

#: Actors first seen through a review or a Jira transition have no commit
#: count, so the commit-count band rule cannot classify them. Recorded as its
#: own basis value rather than quietly defaulting to 'mid'.
NON_COMMIT_BAND = "mid"

#: Columns a later source may FILL but never OVERWRITE. Everything else takes
#: the newest non-null value.
FILL_ONLY = frozenset({"component"})


@dataclass
class Stats:
    pr_payloads: int = 0
    jira_payloads: int = 0
    events: dict[str, int] = field(default_factory=dict)
    work_items: int = 0
    actors: int = 0
    ci_events: int = 0
    ci_unmatched: int = 0
    prs_without_git: int = 0
    skipped_bot_events: int = 0
    key_source: dict[str, int] = field(default_factory=dict)
    repaired_cases: int = 0
    repaired_events: int = 0
    #: PRs both created and merged before the HISTORY_MONTHS boundary. Skipped,
    #: never deleted from raw_payload - the raw layer keeps what it fetched.
    prs_outside_window: int = 0
    #: PRs created before the boundary but merged, or still open, inside it.
    #: Kept, and counted because filtering on createdAt alone would drop them.
    prs_created_before_window_kept: int = 0

    def add(self, activity: str, n: int = 1) -> None:
        self.events[activity] = self.events.get(activity, 0) + n

    @property
    def total_events(self) -> int:
        return sum(self.events.values())


# ---------------------------------------------------------------------
# Identity of an event
# ---------------------------------------------------------------------


def event_id_for(*parts: str) -> str:
    """Deterministic id from the evidence that produced the event.

    Two reviewers requested in the same second on the same PR are two events;
    the reviewer's hash is part of the id so they do not collapse into one.
    """
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]


def parse_ts(value: str | None) -> datetime | None:
    """GitHub returns Z, Jira Server returns +0000. Both, or nothing."""
    if not value:
        return None
    raw = value.strip()
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        pass
    # Jira Server writes +0000 with no colon, which fromisoformat rejects.
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(raw, fmt).astimezone(UTC)
        except ValueError:
            continue
    logger.warning("unparseable timestamp %r", value)
    return None


# ---------------------------------------------------------------------
# Case identity
# ---------------------------------------------------------------------


# ---------------------------------------------------------------------
# The ingestion window
# ---------------------------------------------------------------------


def window_cutoff(now: datetime | None = None) -> datetime:
    """The HISTORY_MONTHS boundary, computed exactly as the ingesters do.

    `github_connector` pages by UPDATED_AT DESC and stops here, which is the
    right filter for "was this active recently" and the wrong one for "did this
    happen recently": a 2023 PR re-enters the feed the moment somebody comments
    on it, carrying its 2023 timestamps. The connector's own
    `_content_predates_window` docstring says so and deliberately does not drop
    the row - it counts it and leaves the decision to this module.

    `now` is injectable so tests do not depend on the wall clock; production
    passes nothing, matching git_local and github_connector.
    """
    months = get_settings().history_months
    return (now or datetime.now(UTC)) - timedelta(days=round(months * DAYS_PER_MONTH))


def within_window(
    payloads: Sequence[tuple[str, dict[str, Any]]],
    stats: Stats,
    now: datetime | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    """Drop PRs that both opened and landed before the boundary. Counts, never deletes.

    THE TEST IS `mergedAt or createdAt`, NOT `createdAt`. That is the
    connector's own rule, restated so the fetch boundary and the mapping
    boundary cannot disagree about which PRs belong to the window. Filtering on
    `createdAt` alone is too aggressive: measured against the live API, 788 PRs
    were created before the boundary but are still active or completed inside
    it, carrying 1,403 in-window events and 127 merges. A PR opened in 2023 and
    merged last month is in-window work whatever its creation date says.

    What goes, correctly, is a PR both created and merged before the boundary,
    in `raw_payload` only because someone commented on it recently. Mapping
    those stretched the event window to 2015-2026 on a log the UI labels twelve
    months, stretched the sprint grid from ~26 windows to 289, and produced
    most of check 2's "merged with no commit" - git_local clones twelve months,
    so those commits cannot be there to join to.

    Skipping is not deleting. The payloads stay in `raw_payload`, so turning
    the filter off with --no-window-filter re-maps them without a re-fetch.
    """
    cutoff = window_cutoff(now)
    kept: list[tuple[str, dict[str, Any]]] = []
    for entity_id, node in payloads:
        created = parse_ts(node.get("createdAt"))
        # mergedAt first: it is the stamp that says when the work landed.
        # Falling back to createdAt judges an unmerged PR on the only date it
        # has, which is what the connector does.
        stamp = parse_ts(node.get("mergedAt")) or created
        if stamp is not None and stamp < cutoff:
            stats.prs_outside_window += 1
            continue
        if created is not None and created < cutoff:
            stats.prs_created_before_window_kept += 1
        kept.append((entity_id, node))
    return kept


def ticket_key_from_pr(node: dict[str, Any], repo: str) -> tuple[str | None, str]:
    """Decision #6's chain: PR title, then branch, then body. Returns the field too.

    Order matters and is not arbitrary. A title is written deliberately; a
    branch name is written once and rarely corrected; a body may quote three
    other tickets in a list of related work. Most reliable field first, and
    only keys that pass `is_real_ticket` count — otherwise the first CVE
    number in a security PR's body becomes the case.
    """
    for field_name in ("title", "headRefName", "body"):
        value = node.get(field_name)
        if not isinstance(value, str):
            continue
        for match in TICKET_ANYWHERE.finditer(value):
            if is_real_ticket(match.group(1), repo):
                return match.group(1), field_name
    return None, "none"


def _known_components(session: Session) -> dict[str, frozenset[str]]:
    """Components git has actually seen, per repo — the top-level directories."""
    rows = session.execute(
        select(WorkItem.repo, WorkItem.component).where(WorkItem.component.isnot(None))
    ).all()
    out: dict[str, set[str]] = {}
    for repo, component in rows:
        out.setdefault(repo, set()).add(component.lower())
    return {repo: frozenset(names) for repo, names in out.items()}


def component_from_labels(labels: list[str], known: frozenset[str]) -> str | None:
    """A label becomes a component only if git independently found that component.

    No hand-written taxonomy. `core`, `clients` and `streams` are apache/kafka
    labels AND top-level directories, so they agree; `triage`, `KIP` and
    `stale-assigned` are labels and nothing else, so they are ignored without
    anyone having to enumerate them. If the repository layout changes, the set
    changes with it.
    """
    for label in labels:
        if label.lower() in known:
            return label.lower()
    return None


def case_for_pr(node: dict[str, Any], repo: str) -> tuple[str, str, str]:
    """Case id, provenance, and the field the key came from.

    The `repo#number` form is not a coincidence: git_local already emits it for
    a commit whose subject ends `(#23015)`, so a PR merges into the case its
    own commits created instead of opening a parallel one beside it.
    """
    key, found_in = ticket_key_from_pr(node, repo)
    if key:
        return key, "ticket_key", found_in
    return f"{repo}#{node['number']}", "pr", "none"


# ---------------------------------------------------------------------
# Actors
# ---------------------------------------------------------------------


def actor_from(node: Any) -> str | None:
    """Pull an actor_hash out of an already-scrubbed author node.

    Returns None for bots and for the deleted-account nodes GitHub returns as
    a bare null. Bots must never become actors: Apache repos are heavily
    automated and an unfiltered bot outranks every human on every metric.
    """
    if not isinstance(node, dict):
        return None
    if node.get("is_bot"):
        return None
    digest = node.get("actor_hash")
    return digest if isinstance(digest, str) and digest else None


def absence_reason(node: Any) -> str | None:
    """Why an event has no actor, recorded rather than left to be guessed.

    Measured on apache/kafka: 95 force-pushes by a bot and 15 PRs whose author
    is a deleted account. Both are legitimate reasons for a null resource, and
    without this attribute the verification report cannot tell either of them
    apart from a mapping bug that silently lost the author.
    """
    if isinstance(node, dict) and node.get("is_bot"):
        return "bot"
    if isinstance(node, dict) and node.get("actor_hash"):
        return None
    return "unattributed"


def ensure_actors(
    session: Session, seen: dict[str, datetime], stats: Stats
) -> set[str]:
    """Insert actors that only ever appear as reviewers or Jira participants.

    `event_log.actor_hash` is a foreign key, so an actor who reviewed but never
    committed would silently drop every review they ever did. Their band cannot
    come from the commit-count rule — they have no commits — so it is recorded
    as the placeholder band with band_basis 'inferred', which is what every
    band in this database is and what the UI is required to say.
    """
    if not seen:
        return set()
    existing = {
        row[0]
        for row in session.execute(
            select(Actor.actor_hash).where(Actor.actor_hash.in_(list(seen)))
        )
    }
    rows = [
        {
            "actor_hash": digest,
            "role_band": NON_COMMIT_BAND,
            "tenure_bucket": infer_tenure(first, first),
            "first_seen": first,
            "band_basis": "inferred",
        }
        for digest, first in seen.items()
        if digest not in existing
    ]
    if rows:
        assert_no_identity(rows)
        stmt = insert(Actor).values(rows)
        session.execute(stmt.on_conflict_do_nothing(index_elements=["actor_hash"]))
        stats.actors += len(rows)
    return set(seen)


# ---------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------


#: Postgres accepts at most 65,535 bind parameters in one statement. A single
#: apache/kafka run produces tens of thousands of event rows, so every bulk
#: write is chunked; the number is small enough to stay under the ceiling for
#: any row width we will ever have.
CHUNK = 1000


def _chunks(rows: list[dict[str, Any]]) -> Iterable[list[dict[str, Any]]]:
    for start in range(0, len(rows), CHUNK):
        yield rows[start : start + CHUNK]


def write_events(session: Session, rows: list[dict[str, Any]]) -> int:
    """Upsert on event_id. attrs is refreshed; a re-run must not duplicate."""
    if not rows:
        return 0
    for row in rows:
        assert_writable(row["activity"])
    assert_no_identity(rows)
    deduped = list({row["event_id"]: row for row in rows}.values())
    for chunk in _chunks(deduped):
        stmt = insert(EventLog).values(chunk)
        session.execute(
            stmt.on_conflict_do_update(
                index_elements=["event_id"],
                set_={
                    "attrs": stmt.excluded.attrs,
                    "ts": stmt.excluded.ts,
                    # actor_hash MUST be in here. Without it, correcting who an
                    # event belongs to silently does nothing on every event
                    # already mapped — the mergedBy fix updated attrs, left the
                    # author in place, and only showed up as six merges whose
                    # actor was null while attrs claimed no reason for it.
                    "actor_hash": stmt.excluded.actor_hash,
                    "duration_s": stmt.excluded.duration_s,
                },
            )
        )
    return len(deduped)


def upsert_work_items(session: Session, rows: list[dict[str, Any]]) -> int:
    """Enrich cases without letting one source blank another's columns.

    COALESCE(excluded, existing) throughout: a PR knows nothing about
    issue_type, and a re-run of the PR mapper at hour 30 must not erase the
    forty minutes the Jira connector spent fetching it.
    """
    if not rows:
        return 0
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        target = merged.setdefault(row["work_item_id"], {})
        for key, value in row.items():
            if value is None:
                continue
            current = target.get(key)
            # LAST WRITE WINS IS WRONG FOR THE DATE COLUMNS. A Jira ticket with
            # three PRs against it arrives as three rows in one batch; taking
            # the last leaves opened_at at whichever PR happened to be last in
            # the page, and the ON CONFLICT LEAST below never sees the earlier
            # one. That produced 2,284 events timestamped before their own case
            # opened before this was a min().
            if key == "opened_at" and current is not None:
                target[key] = min(current, value)
            elif key == "closed_at" and current is not None:
                target[key] = max(current, value)
            else:
                target[key] = value
    # A multi-row INSERT needs one key set. Merging dropped the Nones so a
    # later payload could not blank an earlier one; the gaps go back in here.
    keys = {k for row in merged.values() for k in row}
    values = [{k: row.get(k) for k in keys} for row in merged.values()]
    assert_no_identity(values)
    columns = keys - {"work_item_id", "repo", "opened_at", "case_source"}
    for chunk in _chunks(values):
        stmt = insert(WorkItem).values(chunk)
        session.execute(
            stmt.on_conflict_do_update(
                index_elements=["work_item_id"],
                set_={
                    # COALESCE(new, old) everywhere except FILL_ONLY, where the
                    # existing value wins: git derives component from file
                    # paths and is authoritative for it, so a label-derived
                    # guess may fill a null but must never overwrite one.
                    col: func.coalesce(
                        getattr(WorkItem, col), getattr(stmt.excluded, col)
                    )
                    if col in FILL_ONLY
                    else func.coalesce(
                        getattr(stmt.excluded, col), getattr(WorkItem, col)
                    )
                    for col in columns
                }
                # A case opened when its earliest evidence appeared, whichever
                # source found it first.
                | {
                    "opened_at": func.least(
                        WorkItem.opened_at, stmt.excluded.opened_at
                    )
                },
            )
        )
    return len(values)


# ---------------------------------------------------------------------
# Pull requests
# ---------------------------------------------------------------------


def map_pull_requests(
    session: Session, stats: Stats, window: bool = True
) -> None:
    """github_graphql payloads -> merged / review_requested / review / etc.

    `pr_opened` is deliberately absent: it is not in the frozen vocabulary.
    The PR's creation time lands on work_item.opened_at instead, which is
    where a case-level fact belongs. See activities.REQUESTED_TO_CANONICAL.

    `window=False` maps every landed PR, including ones that closed years
    before the ingestion boundary. See `within_window` for why that is not the
    default.
    """
    payloads: Sequence[tuple[str, dict[str, Any]]] = [
        (row[0], row[1])
        for row in session.execute(
            select(RawPayload.entity_id, RawPayload.body).where(
                RawPayload.source == "github_graphql",
                RawPayload.entity_type == "pull_request",
            )
        ).all()
    ]
    if window:
        payloads = within_window(payloads, stats)

    # Cases git already built from a commit subject ending `(#23015)`. Used
    # below to veto a ticket key that only ever appeared in a PR body.
    git_pr_cases = {
        row[0]
        for row in session.execute(
            select(WorkItem.work_item_id).where(WorkItem.case_source == "pr")
        )
    }
    known_components = _known_components(session)

    events: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    actors_seen: dict[str, datetime] = {}

    def note(digest: str | None, ts: datetime | None) -> str | None:
        if digest and ts and (digest not in actors_seen or ts < actors_seen[digest]):
            actors_seen[digest] = ts
        return digest

    for entity_id, node in payloads:
        stats.pr_payloads += 1
        repo = node.get("repo") or entity_id.split("#", 1)[0]
        case_id, case_source, key_from = case_for_pr(node, repo)
        number = node["number"]

        # A BODY MENTION IS NOT A CLAIM OF IDENTITY, AND TAKING IT AS ONE
        # SPLITS ONE PR ACROSS TWO CASES.
        #
        # Decision #6's chain is title, then branch, then body. Measured on
        # 5,632 apache PRs the body supplies 2.2% of keys and is the only
        # field that ever contradicts git: PR 20695 is titled `MINOR: prevent
        # joint compilation error`, its commit subject ends `(#20695)`, and
        # its body says "related to KAFKA-19786". git files the commit under
        # apache/kafka#20695 and the body would file the reviews under
        # KAFKA-19786 — one piece of work, two cases, and every review on it
        # timestamped before its own case opened.
        #
        # The title and branch still win outright. The body is only refused
        # when git has independently built a case for this PR number, which is
        # the one situation where it is demonstrably describing related work
        # rather than naming itself.
        if key_from == "body" and f"{repo}#{number}" in git_pr_cases:
            case_id, case_source = f"{repo}#{number}", "pr"
            key_from = "body (vetoed by git)"
        stats.key_source[key_from] = stats.key_source.get(key_from, 0) + 1
        created = parse_ts(node.get("createdAt"))
        author = note(actor_from(node.get("author")), created)

        labels = [str(x) for x in (node.get("labels") or []) if x]
        items.append(
            {
                "work_item_id": case_id,
                "repo": repo,
                "case_source": case_source,
                "opened_at": created,
                "closed_at": parse_ts(node.get("closedAt")),
                "source_ref": f"{repo}#{number}",
                "jira_key": case_id if case_source == "ticket_key" else None,
                # THE MILESTONE IS ALWAYS NULL ON APACHE. Measured: 5,632 PRs,
                # zero milestones — the projects do not use the feature. So
                # the epic comes from the improvement proposal the PR cites,
                # which is a better epic anyway: KIP-1071 is a named body of
                # work spanning many PRs over months, which is exactly the
                # unit a spend treemap wants to group by. The milestone stays
                # as a fallback because it costs nothing and another repo may
                # actually use it.
                "epic": epic_from_text(node.get("title"), node.get("body"))
                or (node.get("milestone") or {}).get("title"),
                # Component is derived from file paths and git is authoritative
                # for it — this only fills the gap for the 1,391 PR cases that
                # have no commit in the clone, where the alternative is null.
                "component": component_from_labels(
                    labels, known_components.get(repo, frozenset())
                ),
            }
        )

        base = ("github_graphql", "pull_request", f"{repo}#{number}")
        merge_sha = (node.get("mergeCommit") or {}).get("oid")

        merged_at = parse_ts(node.get("mergedAt"))
        if merged_at:
            # THE MERGER, NOT THE AUTHOR. Using the author made every merge
            # unattributable to the person who actually took the decision —
            # and on apache the two are rarely the same human, since a
            # committer merges a contributor's PR.
            merger = note(actor_from(node.get("mergedBy")), merged_at)
            events.append(
                {
                    "event_id": event_id_for(*base, "merged", merged_at.isoformat()),
                    "work_item_id": case_id,
                    "actor_hash": merger,
                    "activity": "merged",
                    "ts": merged_at,
                    "source": "github",
                    "attrs": {
                        "ingest_source": "github_graphql",
                        "pr_number": number,
                        "merge_commit_sha": merge_sha,
                        "changed_files": node.get("changedFiles"),
                        # Descriptive only. Never an effort proxy — hard rule.
                        "additions": node.get("additions"),
                        "deletions": node.get("deletions"),
                        "actor_absent": absence_reason(node.get("mergedBy")),
                        "authored_by": author,
                    },
                }
            )
            stats.add("merged")

        for review in (node.get("reviews") or {}).get("nodes") or []:
            if not review:
                continue
            activity = REVIEW_STATE_TO_ACTIVITY.get(review.get("state") or "")
            ts = parse_ts(review.get("submittedAt"))
            if not activity or not ts:
                continue
            reviewer = note(actor_from(review.get("author")), ts)
            if reviewer is None:
                stats.skipped_bot_events += 1
                continue
            events.append(
                {
                    "event_id": event_id_for(
                        *base, activity, ts.isoformat(), reviewer
                    ),
                    "work_item_id": case_id,
                    "actor_hash": reviewer,
                    "activity": activity,
                    "ts": ts,
                    "source": "github",
                    "attrs": {
                        "ingest_source": "github_graphql",
                        "pr_number": number,
                        "review_state": review.get("state"),
                    },
                }
            )
            stats.add(activity)

        for item in (node.get("timelineItems") or {}).get("nodes") or []:
            if not item:
                continue
            activity = TIMELINE_TO_ACTIVITY.get(item.get("__typename") or "")
            ts = parse_ts(item.get("createdAt"))
            if not activity or not ts:
                continue
            who = note(actor_from(item.get("actor")), ts)
            reviewer = item.get("requestedReviewer") or {}
            target = (
                reviewer.get("actor_hash")
                or reviewer.get("slug")  # a team, not a person
                or ""
            )
            events.append(
                {
                    "event_id": event_id_for(*base, activity, ts.isoformat(), target),
                    "work_item_id": case_id,
                    "actor_hash": who,
                    "activity": activity,
                    "ts": ts,
                    "source": "github",
                    "attrs": {
                        "ingest_source": "github_graphql",
                        "pr_number": number,
                        "requested_reviewer": target or None,
                        "actor_absent": absence_reason(item.get("actor")),
                    },
                }
            )
            stats.add(activity)

        # P2: squash-merge collapses trunk history to one commit per PR
        # (measured: avg 1.18 commits/work_item, median 1.0), leaving session
        # inference nothing to cluster. github_connector.PR_QUERY now fetches
        # each PR's own pre-squash commits; land them here.
        for commit_node in (node.get("commits") or {}).get("nodes") or []:
            if not commit_node:
                continue
            commit = commit_node.get("commit") or {}
            oid = commit.get("oid")
            ts = parse_ts(commit.get("authoredDate"))
            if not oid or not ts:
                continue
            author_node = (commit.get("author") or {}).get("user")
            if isinstance(author_node, dict) and author_node.get("is_bot"):
                stats.skipped_bot_events += 1
                continue
            committer = note(actor_from(author_node), ts)
            events.append(
                {
                    # Byte-identical to git_local.event_id_for on the same
                    # (sha, authored_at) — see that function's docstring. A
                    # commit git_local already saw on trunk (a non-squash
                    # merge) converges onto that row via write_events'
                    # on_conflict_do_update instead of duplicating; a commit
                    # squashed away before it ever reached trunk has no
                    # existing row and lands here for the first time.
                    "event_id": event_id_for(
                        "git_local", "commit", str(oid), "commit", ts.isoformat()
                    ),
                    "work_item_id": case_id,
                    "actor_hash": committer,
                    "activity": "commit",
                    "ts": ts,
                    "source": "github",
                    "attrs": {
                        "ingest_source": "github_graphql",
                        "sha": oid,
                        "pr_number": number,
                        "additions": commit.get("additions"),
                        "deletions": commit.get("deletions"),
                        "changed_files": commit.get("changedFiles"),
                        "is_squash_merge": bool(merge_sha) and oid == merge_sha,
                        "actor_absent": absence_reason(author_node),
                    },
                }
            )
            stats.add("commit")

    stats.work_items += upsert_work_items(session, items)
    ensure_actors(session, actors_seen, stats)
    write_events(session, events)
    session.commit()


# ---------------------------------------------------------------------
# Jira
# ---------------------------------------------------------------------


def map_jira(session: Session, stats: Stats) -> None:
    """asf_jira payloads -> ticket_created + one event per status transition.

    The changelog is the event source, not the issue. An issue's current
    status tells you where it ended; the changelog tells you how long it sat
    in each state on the way, which is the entire point.
    """
    spines = dict(
        session.execute(
            select(RawPayload.entity_id, RawPayload.body).where(
                RawPayload.source == "asf_jira", RawPayload.entity_type == "issue"
            )
        ).all()
    )
    changelogs = dict(
        session.execute(
            select(RawPayload.entity_id, RawPayload.body).where(
                RawPayload.source == "asf_jira", RawPayload.entity_type == "changelog"
            )
        ).all()
    )

    events: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    actors_seen: dict[str, datetime] = {}
    unmapped_status: dict[str, int] = {}

    for key, spine in spines.items():
        stats.jira_payloads += 1
        fields = spine.get("fields") or {}
        project = key.split("-", 1)[0]
        repo = PROJECT_TO_REPO.get(project)
        if repo is None:
            logger.warning("no repo mapped for Jira project %s; skipping %s", project, key)
            continue

        created = parse_ts(fields.get("created"))
        components = fields.get("components") or []
        items.append(
            {
                "work_item_id": key,
                "repo": repo,
                "case_source": "issue",
                "jira_key": key,
                "component": (components[0] or {}).get("name") if components else None,
                "issue_type": (fields.get("issuetype") or {}).get("name"),
                "priority": (fields.get("priority") or {}).get("name"),
                "resolution": (fields.get("resolution") or {}).get("name"),
                "parent_key": (fields.get("parent") or {}).get("key"),
                # ASF's Epic Link is a custom field we do not fetch, so the
                # parent issue is the closest honest equivalent: the bigger
                # piece of work this one belongs to. GitHub cases get theirs
                # from the milestone instead.
                "epic": (fields.get("parent") or {}).get("key"),
                "jira_created_at": created,
                "jira_resolved_at": parse_ts(fields.get("resolutiondate")),
                "opened_at": created,
                "source_ref": key,
            }
        )

        if created:
            events.append(
                {
                    "event_id": event_id_for("asf_jira", "issue", key, "ticket_created"),
                    "work_item_id": key,
                    "actor_hash": None,  # the spine carries no reporter after scrubbing
                    "activity": "ticket_created",
                    "ts": created,
                    "source": "jira",
                    "attrs": {
                        "ingest_source": "asf_jira",
                        "jira_key": key,
                        "issue_type": (fields.get("issuetype") or {}).get("name"),
                    },
                }
            )
            stats.add("ticket_created")

        for history in (changelogs.get(key) or {}).get("histories") or []:
            ts = parse_ts(history.get("created"))
            if not ts:
                continue
            who = actor_from(history.get("author"))
            if who and (who not in actors_seen or ts < actors_seen[who]):
                actors_seen[who] = ts
            for index, item in enumerate(history.get("items") or []):
                if item.get("field") != "status":
                    continue
                to_status = (item.get("toString") or "").strip()
                activity = JIRA_STATUS_TO_ACTIVITY.get(to_status.lower())
                if activity is None:
                    unmapped_status[to_status] = unmapped_status.get(to_status, 0) + 1
                    continue
                events.append(
                    {
                        "event_id": event_id_for(
                            "asf_jira",
                            "changelog",
                            key,
                            str(history.get("id") or ts.isoformat()),
                            str(index),
                        ),
                        "work_item_id": key,
                        "actor_hash": who,
                        "activity": activity,
                        "ts": ts,
                        "source": "jira",
                        "attrs": {
                            "ingest_source": "asf_jira",
                            "jira_key": key,
                            # The raw transition survives the mapping, so a
                            # different grouping never needs a re-fetch.
                            "from_status": item.get("fromString"),
                            "to_status": to_status,
                        },
                    }
                )
                stats.add(activity)

    if unmapped_status:
        logger.warning("unmapped Jira statuses (no event written): %s", unmapped_status)

    stats.work_items += upsert_work_items(session, items)
    ensure_actors(session, actors_seen, stats)
    write_events(session, events)
    session.commit()


# ---------------------------------------------------------------------
# CI
# ---------------------------------------------------------------------


def repair_invalid_cases(session: Session, stats: Stats) -> None:
    """Move events off cases whose id is not a Jira key of this repo's project.

    git ingestion ran before `is_real_ticket` existed and filed 63 cases under
    KIP-909, CVE-2026, SHA-256, UTF-8, GPT-5 and friends. Event ids are hashes
    of the commit, not of the case, so a re-run of the connector upserts the
    same rows and leaves them exactly where they are — the only thing that
    moves them is an UPDATE.

    Each event goes to the case its own evidence supports: `repo#pr` when the
    commit subject named a PR, `repo@sha12` otherwise. Same fallback chain,
    same shape, so nothing is orphaned and nothing is deleted.
    """
    rows = session.execute(
        text(
            """
            SELECT e.event_id, e.work_item_id, w.repo, e.attrs->>'sha',
                   COALESCE(e.attrs->>'pr_number', r.body->>'subject')
              FROM event_log e
              JOIN work_item w USING (work_item_id)
              LEFT JOIN raw_payload r
                ON r.source = 'git_local' AND r.entity_type = 'commit'
               AND r.entity_id = e.attrs->>'sha'
             WHERE w.case_source = 'ticket_key'
            """
        )
    ).all()

    moves: list[dict[str, str]] = []
    new_items: list[dict[str, Any]] = []
    for event_id, case_id, repo, sha, pr_hint in rows:
        if is_real_ticket(case_id, repo):
            continue
        # The commit subject is the only place the PR number survives for a
        # git event, and it is in raw_payload rather than attrs. Landing raw
        # first is what makes this repairable at all.
        pr_number = None
        if pr_hint:
            pr_number = pr_hint if pr_hint.isdigit() else None
            if pr_number is None:
                match = PR_RE.search(pr_hint)
                pr_number = match.group(1) if match else None
        if pr_number:
            target = f"{repo}#{pr_number}"
        elif sha:
            target = f"{repo}@{sha[:12]}"
        else:
            # No independent evidence to re-file it under; leaving it where it
            # is beats inventing a case for it.
            continue
        moves.append({"eid": event_id, "target": target})
        new_items.append(
            {"work_item_id": target, "repo": repo, "case_source": "pr"}
        )

    if moves:
        upsert_work_items(session, new_items)
        session.flush()
        for chunk in _chunks(moves):  # type: ignore[arg-type]
            session.execute(
                text(
                    "UPDATE event_log SET work_item_id = :target "
                    "WHERE event_id = :eid"
                ),
                chunk,
            )

    # CI is the other half of the same repair and needs different treatment.
    # A ci_run event carries `head_sha`, not `sha`, so the loop above cannot
    # re-file it; and ci_run holds its own foreign key to the same bad case,
    # which keeps `CVE-2026` alive through the orphan sweep and lets
    # map_ci_runs write 124 fresh events straight back onto it.
    #
    # So: drop those events and null the foreign key. rejoin_ci_runs re-derives
    # the case from head_sha and map_ci_runs rebuilds the events from the
    # ci_run rows, which are never deleted. Nothing is lost — the run is still
    # in ci_run either way, which is the whole reason that table exists.
    invalid_case = """
        w.case_source = 'ticket_key'
        AND NOT (split_part(w.work_item_id, '-', 1) = ANY(:projects))
    """
    projects = {"projects": list(PROJECT_TO_REPO)}
    session.execute(
        text(
            f"""
            DELETE FROM event_log e USING work_item w
             WHERE w.work_item_id = e.work_item_id
               AND e.activity = 'ci_run' AND {invalid_case}
            """
        ),
        projects,
    )
    session.execute(
        text(
            f"""
            UPDATE ci_run c SET work_item_id = NULL
              FROM work_item w
             WHERE w.work_item_id = c.work_item_id AND {invalid_case}
            """
        ),
        projects,
    )
    # Only the cases this function emptied. An unqualified "delete every case
    # with no events" also removes open PRs that have not been reviewed yet —
    # legitimate cases with no activity — and the PR mapper simply recreates
    # them thirty seconds later, so the sweep would churn 616 rows a run and
    # report them as repairs.
    orphans = session.execute(
        text(
            f"""
            DELETE FROM work_item w
             WHERE {invalid_case}
               AND NOT EXISTS (SELECT 1 FROM event_log e
                                WHERE e.work_item_id = w.work_item_id)
               AND NOT EXISTS (SELECT 1 FROM ci_run c
                                WHERE c.work_item_id = w.work_item_id)
               AND w.jira_created_at IS NULL
            """
        ),
        projects,
    ).rowcount
    stats.repaired_events += len(moves)
    stats.repaired_cases += orphans or 0
    session.commit()
    logger.info(
        "repaired %d events off invalid cases; dropped %d now-empty work items",
        len(moves),
        orphans,
    )


def rejoin_ci_runs(session: Session) -> int:
    """Re-join ci_run to every sha a case owns, not just the ones git saw.

    Rule 4. The Actions connector joins on commit shas from the local clone.
    That misses the merge commit, which is exactly the sha most CI runs are
    triggered on — GitHub runs the merge result, not the branch tip. Adding
    `mergeCommit.oid` from the PR payloads recovers those runs.
    """
    result = session.execute(
        text(
            """
            WITH sha_case AS (
                SELECT attrs->>'sha' AS sha, work_item_id
                FROM event_log
                WHERE activity = 'commit' AND attrs ? 'sha'
                UNION
                SELECT attrs->>'merge_commit_sha', work_item_id
                FROM event_log
                WHERE activity = 'merged' AND attrs->>'merge_commit_sha' IS NOT NULL
            )
            UPDATE ci_run c
               SET work_item_id = s.work_item_id
              FROM (SELECT DISTINCT ON (sha) sha, work_item_id
                      FROM sha_case ORDER BY sha, work_item_id) s
             WHERE c.head_sha = s.sha
               AND c.work_item_id IS DISTINCT FROM s.work_item_id
            """
        )
    )
    session.commit()
    return result.rowcount or 0


def map_ci_runs(session: Session, stats: Stats) -> None:
    """ci_run -> one `ci_run` event per attributable run.

    ci_started / ci_completed / ci_rerun collapse into one event carrying both
    timestamps: duration_s is the span, attrs.attempt distinguishes the rerun.
    Emitting a start and a complete would double every case sequence and make
    the variant graph unreadable for no information gained.

    Runs with no work item keep their row in `ci_run` and are counted in the
    report. event_log.work_item_id is NOT NULL, so they cannot become events —
    that is the one place the brief's "preserve with a NULL FK" rule collides
    with the frozen schema, and the row is preserved rather than dropped.
    """
    rows = session.execute(
        text(
            """
            SELECT run_id, work_item_id, repo, head_sha, ts, runner_minutes,
                   conclusion, attempt
            FROM ci_run
            WHERE work_item_id IS NOT NULL
            """
        )
    ).all()
    stats.ci_unmatched = (
        session.execute(
            text("SELECT count(*) FROM ci_run WHERE work_item_id IS NULL")
        ).scalar_one()
        or 0
    )

    events = []
    for run_id, case_id, repo, head_sha, ts, minutes, conclusion, attempt in rows:
        minutes = float(minutes or 0)
        events.append(
            {
                "event_id": event_id_for("github_actions", "workflow_run", str(run_id)),
                "work_item_id": case_id,
                "actor_hash": None,  # a CI run is not a person; the column is nullable
                "activity": "ci_run",
                "ts": ts,
                "duration_s": minutes * 60.0,
                "source": "github",
                "attrs": {
                    "ingest_source": "github_actions",
                    "run_id": run_id,
                    "repo": repo,
                    "head_sha": head_sha,
                    "conclusion": conclusion,
                    "attempt": attempt,
                    "is_rerun": (attempt or 1) > 1,
                    "completed_at": (ts + timedelta(minutes=minutes)).isoformat(),
                    "duration_basis": "wall_clock",
                },
            }
        )
    stats.add("ci_run", len(events))
    stats.ci_events = len(events)
    write_events(session, events)

    # Upsert alone is not idempotent in the direction that matters here. A
    # ci_run row that goes away — re-fetched into a different window, or
    # deleted — leaves its event behind forever, and the CI event count then
    # exceeds the CI table it is derived from. Events are only ever removed
    # when the run backing them no longer exists.
    stale = session.execute(
        text(
            """
            DELETE FROM event_log e
             WHERE e.activity = 'ci_run'
               AND NOT EXISTS (
                     SELECT 1 FROM ci_run c
                      WHERE c.run_id = e.attrs->>'run_id'
                        AND c.work_item_id IS NOT NULL)
            """
        )
    ).rowcount
    if stale:
        logger.info("dropped %d ci_run events with no surviving run", stale)
    session.commit()


# ---------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------


def record_cutoff(session: Session) -> datetime:
    # One definition of the boundary, shared with the PR filter. These used to
    # be the same expression written twice; they must never drift, because
    # v_event_log.in_window is computed from what lands here.
    cutoff = window_cutoff()
    stmt = insert(RunConfig).values(
        key=CUTOFF_KEY, value=cutoff.isoformat(), note=CUTOFF_NOTE
    )
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["key"],
            set_={"value": stmt.excluded.value, "note": stmt.excluded.note},
        )
    )
    session.commit()
    return cutoff


#: docs/schema.sql is applied once by docker initdb and is frozen, so the two
#: views this layer adds have to be applied separately. Doing it here rather
#: than in a setup script means a teammate who pulls and runs the normaliser
#: gets them without being told; every statement in the file is CREATE OR
#: REPLACE, so re-running costs nothing.
VIEWS_SQL = Path(__file__).resolve().parents[2] / "migrations" / "002_canonical_event_log.sql"


def apply_views(session: Session) -> None:
    session.execute(text(VIEWS_SQL.read_text(encoding="utf-8")))
    session.commit()


def build(session: Session, window: bool = True) -> Stats:
    stats = Stats()
    apply_views(session)
    record_cutoff(session)
    # Repair first: a commit sitting on a bogus `SHA-256` case has to move to
    # `apache/kafka#23112` BEFORE the PR mapper lands PR 23112, or the two
    # halves of one piece of work end up in two different cases.
    repair_invalid_cases(session, stats)
    map_pull_requests(session, stats, window=window)
    map_jira(session, stats)
    rejoined = rejoin_ci_runs(session)
    logger.info("re-joined %d ci_run rows to a work item", rejoined)
    map_ci_runs(session, stats)
    # Sprints are a global 14-day grid over ALL events, so they must be
    # recomputed after new sources widen the time axis.
    assign_sprints(session, get_settings().sprint_days)
    session.commit()
    return stats


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Build the canonical event log.")
    parser.add_argument("--verify", action="store_true", help="print the report only")
    parser.add_argument(
        "--no-window-filter",
        dest="window",
        action="store_false",
        help="map every landed PR, including ones created AND merged before "
        "the HISTORY_MONTHS boundary. On by default; see within_window.",
    )
    args = parser.parse_args(argv)

    from app.normalise.verify import print_report

    with write_session() as session:
        if not args.verify:
            stats = build(session, window=args.window)
            print(f"\nmapped {stats.total_events:,} events from "
                  f"{stats.pr_payloads:,} PR and {stats.jira_payloads:,} Jira payloads")
            months = get_settings().history_months
            if args.window:
                print(f"\n  the {months}-month window "
                      "(rule: mergedAt or createdAt)")
                print(f"    PRs skipped             {stats.prs_outside_window:>6}  "
                      "(created AND merged before it)")
                print("    older PRs kept          "
                      f"{stats.prs_created_before_window_kept:>6}  "
                      "(created before it, landed inside)")
                print("    Skipped rows stay in raw_payload; --no-window-filter")
                print("    re-maps them without a re-fetch.")
            else:
                print(f"\n  window filter OFF - every landed PR mapped, "
                      f"including pre-{months}-month work")
        print_report(session)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
