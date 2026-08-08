"""
GitHub mapping — raw_payload -> work_item, event_log, ci_run. NO NETWORK.
Owner: Dipen (normalise lane).
Phase: Tier 0.

    python -m app.normalise.map_github
    python -m app.normalise.map_github --repo apache/kafka --dry-run

Reads `raw_payload` where source is one of `git_local`, `github_graphql`,
`github_actions`. **It never reads `asf_jira`.** Jira mapping is a separate
task and a separate module; the SELECT here is filtered by source so a Jira row
landing in the same table cannot be picked up by accident.

CONVENTIONS ARE INHERITED, NOT INVENTED
---------------------------------------
`app.ingestion.git_local` already wrote commit events, provisional work items
and the global sprint grid. Everything below matches it rather than competing
with it:

* `event_id` = sha256("|".join(source, entity_type, entity_id, activity,
  ts.isoformat()))[:24] — byte-identical to `git_local.event_id_for`.
* case ids are the bare ticket key (`KAFKA-16234`), else `{repo}#{number}`,
  else git_local's provisional `{repo}@{sha[:12]}`.
* `component` is the top-level directory of the majority of files touched,
  ties broken alphabetically, root files as `(root)` — the same rule and the
  same `ROOT_COMPONENT` constant git_local uses, imported rather than copied.
* `case_source` for a PR with no ticket key and no closing issue is `'pr'`,
  which is also what git_local records for its sha placeholder. The schema's
  CHECK allows only ticket_key/issue/pr, so there is no fourth value to use.

git_local resolves ticket_key -> pr -> sha placeholder and has no `'issue'`
rung, because a git commit carries no issue reference. This module inserts
that rung between them, which is decision #6 in full rather than a divergence.

FOUR THINGS THE UPSTREAM DATA CANNOT GIVE US YET
------------------------------------------------
Each is implemented and tested so it works the moment the payload carries it,
and each is reported as a count at the end of a run rather than being silently
absent:

1. `epic` needs `milestone` or `labels`, and `github_connector.PR_QUERY`
   requests neither. Note for whoever adds them: that connector's own
   `_assert_scrubbed` raises on any key named `name`, so `labels { nodes {
   name } }` will trip it and needs handling there first.
2. The `'issue'` rung needs `closingIssuesReferences`, also absent from the
   query.
3. `merged` events carry `actor_hash = NULL`. The query fetches `mergedAt` but
   not `mergedBy`, and attributing a merge to the PR author would be a
   fabricated number in a table whose whole purpose is that its numbers are
   real. NULL is the honest answer and the column is nullable for it.
4. No connector writes `source='github_actions'` yet. The workflow-run mapper
   below is written against the documented Actions REST shape and that shape is
   pinned in `WORKFLOW_RUN_CONTRACT` — match it when the connector lands.

WHY THIS WRITES ci_run AS WELL AS work_item AND event_log
---------------------------------------------------------
The brief said Actions runs become `ci_run` with `work_item_id` left null when
no head_sha matches. `event_log.work_item_id` is NOT NULL in the frozen schema,
so an unmatched run cannot be an event. The `ci_run` table exists with a
nullable `work_item_id` and the comment "nullable: most runs do not map",
which is precisely this case. So: every run is written to `ci_run`, and a run
additionally becomes an `activity='ci_run'` event only once it has a case to
hang off. No run is dropped either way.

SPRINT IS READ, NEVER RECOMPUTED
--------------------------------
`work_item.sprint` is written once by `git_local.assign_sprints` across the
whole event log, so that every lane splits the data identically (decision #7).
This module never computes one. A PR with no in-window commit creates a case
this module has no sprint for, and that case is reported at the end with a
null sprint rather than being given a guessed one. `--assign-sprints` calls
git_local's own function to re-derive the global grid; it is opt-in, it is not
a second implementation, and it is off by default.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import re
import sys
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.db.models import Actor, CiRun, EventLog, RawPayload, WorkItem
from app.db.session import write_session
from app.ingestion.git_local import ROOT_COMPONENT, infer_band, infer_tenure
from app.ingestion.pseudonymize import assert_no_identity
from sqlalchemy import bindparam, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

#: The only sources this module will read. `asf_jira` is deliberately absent.
GITHUB_SOURCES: tuple[str, ...] = ("git_local", "github_graphql", "github_actions")

GIT_SOURCE = "git_local"
PR_SOURCE = "github_graphql"
ACTIONS_SOURCE = "github_actions"

PR_ENTITY = "pull_request"
RUN_ENTITY = "workflow_run"

#: Everything written here is observed GitHub activity (decision #11).
EVENT_SOURCE = "github"

#: Decision #6's regex. Anchored at the start for the title and the branch,
#: where the Apache convention puts the key ("KAFKA-16234: ..."), and searched
#: anywhere in the body, which is the last rung and where a key is more often
#: mentioned in passing. git_local anchors on the commit subject for the same
#: reason: a key referenced mid-sentence must not steal the case.
TICKET_ANCHORED = re.compile(r"^\s*\[?([A-Z]{2,10}-\d+)\]?")
TICKET_ANYWHERE = re.compile(r"\b([A-Z]{2,10}-\d+)\b")

#: GraphQL review states that map to a canonical activity. DISMISSED and
#: PENDING are deliberately absent: neither is in the vocabulary, and a PENDING
#: review has no `submittedAt` to place it on the timeline anyway.
REVIEW_STATE_TO_ACTIVITY: dict[str, str] = {
    "COMMENTED": "review",
    "APPROVED": "approved",
    "CHANGES_REQUESTED": "changes_requested",
}

#: Timeline `__typename` to canonical activity.
TIMELINE_TYPE_TO_ACTIVITY: dict[str, str] = {
    "ReviewRequestedEvent": "review_requested",
    "ReopenedEvent": "reopened",
    "HeadRefForcePushedEvent": "force_push",
}

#: The `raw_payload.body` shape this module expects for a workflow run. No
#: connector produces it yet — see the module docstring. `runner_minutes` is
#: wall clock (`updated_at - run_started_at`), which is what run_config's
#: `ci_duration_basis` already commits us to, not billable runner minutes.
WORKFLOW_RUN_CONTRACT: tuple[str, ...] = (
    "id",
    "repo",
    "head_sha",
    "run_started_at",
    "updated_at",
    "conclusion",
    "run_attempt",
)


@dataclass
class Stats:
    payloads_read: int = 0
    git_local_skipped: int = 0
    pull_requests: int = 0
    workflow_runs: int = 0
    work_items: int = 0
    events: int = 0
    ci_runs: int = 0
    actors: int = 0
    commits_repointed: int = 0
    commits_left_alone: int = 0
    runs_matched: int = 0
    runs_unmatched: int = 0
    bot_events_dropped: int = 0
    unmapped_review_states: Counter = field(default_factory=Counter)
    case_sources: Counter = field(default_factory=Counter)
    activities: Counter = field(default_factory=Counter)
    epics_found: int = 0
    sprintless_cases: int = 0
    orphaned_provisional: int = 0


# =========================================================================
# Identifiers — the git_local scheme, restated once and shared
# =========================================================================


def event_id_for(
    source: str, entity_type: str, entity_id: str, activity: str, ts: datetime
) -> str:
    """Deterministic event id so a re-run upserts instead of duplicating.

    Identical construction to `git_local.event_id_for`, which hardcodes its own
    source/entity_type/activity for commits. A test asserts the two agree on
    the same inputs; if this ever drifts, every commit event would be rewritten
    under a second id and the log would double.
    """
    parts = (source, entity_type, entity_id, activity, ts.isoformat())
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]


def component_of(paths: Iterable[str]) -> str | None:
    """Top-level directory of the majority of files touched.

    The same rule as `git_local.Commit.component`, including the alphabetical
    tie-break: a component that flickers between runs makes every downstream
    cost number irreproducible.
    """
    tops = Counter(
        path.split("/", 1)[0] if "/" in path else ROOT_COMPONENT
        for path in paths
        if path
    )
    if not tops:
        return None
    best = max(tops.values())
    return min(name for name, n in tops.items() if n == best)


def ticket_key_from(title: str | None, branch: str | None, body: str | None) -> str | None:
    """Decision #6 rung 1: the key from the PR title, then branch, then body."""
    for text in (title, branch):
        if text:
            match = TICKET_ANCHORED.match(text)
            if match:
                return match.group(1)
    if body:
        match = TICKET_ANYWHERE.search(body)
        if match:
            return match.group(1)
    return None


def resolve_case(node: dict[str, Any], repo: str) -> tuple[str, str, str | None]:
    """The full fallback chain. Returns (work_item_id, case_source, ticket_key).

    ticket_key -> closing issue -> {repo}#{number}. A PR is never dropped for
    lacking a ticket key; the third rung always succeeds, which is the whole
    point of decision #6.
    """
    key = ticket_key_from(node.get("title"), node.get("headRefName"), node.get("body"))
    if key:
        return key, "ticket_key", key

    for issue in _nodes(node.get("closingIssuesReferences")):
        number = issue.get("number")
        if number is None:
            continue
        issue_repo = (issue.get("repository") or {}).get("nameWithOwner") or repo
        # Issues and PRs share one numbering space per repo, so this can never
        # collide with the `{repo}#{number}` form below.
        return f"{issue_repo}#{number}", "issue", None

    return f"{repo}#{node['number']}", "pr", None


def epic_of(node: dict[str, Any]) -> str | None:
    """Milestone, else the dominant label, else null.

    Neither field is in the connector's query today; see the module docstring.
    """
    milestone = node.get("milestone") or {}
    title = milestone.get("title")
    if title:
        return str(title)
    labels = [
        str(label.get("name"))
        for label in _nodes(node.get("labels"))
        if label.get("name")
    ]
    if not labels:
        return None
    counts = Counter(labels)
    best = max(counts.values())
    return min(name for name, n in counts.items() if n == best)


def _nodes(connection: Any) -> list[dict[str, Any]]:
    """GraphQL connections are `{nodes: [...]}`; any of it may be null."""
    if not isinstance(connection, dict):
        return []
    return [n for n in (connection.get("nodes") or []) if isinstance(n, dict)]


def _actor_hash(node: Any) -> str | None:
    """The hash on an already-scrubbed actor node, or None for a bot/absent.

    The connector replaced every `login` with `{__typename, is_bot,
    actor_hash}` at the fetch boundary, so there is no login here to
    pseudonymise — the hash is simply read. A bot node has no `actor_hash` key
    at all, which is what makes "bots never become actors" structural rather
    than a filter this module has to remember to apply.
    """
    if not isinstance(node, dict):
        return None
    if node.get("is_bot"):
        return None
    digest = node.get("actor_hash")
    return str(digest) if digest else None


def _is_bot(node: Any) -> bool:
    return bool(isinstance(node, dict) and node.get("is_bot"))


def _ts(value: Any) -> datetime | None:
    """Parse an ISO timestamp. Everything upstream is UTC and stays UTC."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        logger.warning("unparseable timestamp %r, skipping", value)
        return None


# =========================================================================
# Pure mapping — no session, no I/O
# =========================================================================


@dataclass
class MappedPR:
    work_item: dict[str, Any]
    events: list[dict[str, Any]]
    merge_commit_sha: str | None
    case_source: str
    bot_events_dropped: int = 0
    unmapped_review_states: Counter = field(default_factory=Counter)


def map_pull_request(body: dict[str, Any], stats: Stats | None = None) -> MappedPR:
    """One `github_graphql` payload -> one work_item and its events.

    Pure: takes a dict, returns dicts. Every branch here is exercised by a
    hand-written fixture in backend/tests/fixtures/.
    """
    repo = str(body.get("repo") or "")
    number = body["number"]
    work_item_id, case_source, ticket_key = resolve_case(body, repo)
    pr_ref = f"{repo}#{number}"

    created = _ts(body.get("createdAt"))
    merged = _ts(body.get("mergedAt"))
    closed = _ts(body.get("closedAt"))
    paths = [str(f.get("path")) for f in _nodes(body.get("files")) if f.get("path")]

    work_item = {
        "work_item_id": work_item_id,
        "repo": repo,
        "component": component_of(paths),
        "epic": epic_of(body),
        "opened_at": created,
        "closed_at": closed or merged,
        "source_ref": pr_ref,
        "case_source": case_source,
        "jira_key": ticket_key,
    }

    events: list[dict[str, Any]] = []
    dropped = 0
    unmapped: Counter = Counter()

    def add(
        activity: str,
        ts: datetime | None,
        entity_id: str,
        actor: str | None,
        attrs: dict[str, Any],
    ) -> None:
        if ts is None:
            return
        events.append(
            {
                "event_id": event_id_for(
                    PR_SOURCE, PR_ENTITY, entity_id, activity, ts
                ),
                "work_item_id": work_item_id,
                "actor_hash": actor,
                "activity": activity,
                "ts": ts,
                "source": EVENT_SOURCE,
                "attrs": attrs,
            }
        )

    # --- reviews
    for review in _nodes(body.get("reviews")):
        state = str(review.get("state") or "")
        activity = REVIEW_STATE_TO_ACTIVITY.get(state)
        if activity is None:
            unmapped[state] += 1
            continue
        author = review.get("author")
        if _is_bot(author):
            dropped += 1
            continue
        digest = _actor_hash(author)
        submitted = _ts(review.get("submittedAt"))
        # The reviewer is part of the entity id: two people can approve the
        # same PR in the same second, and without this they would collapse
        # into one event and one of the reviews would silently vanish.
        add(
            activity,
            submitted,
            f"{pr_ref}:review:{digest or 'anon'}",
            digest,
            {"state": state, "pr": pr_ref},
        )

    # --- timeline
    for item in _nodes(body.get("timelineItems")):
        activity = TIMELINE_TYPE_TO_ACTIVITY.get(str(item.get("__typename") or ""))
        if activity is None:
            continue
        actor = item.get("actor")
        if _is_bot(actor):
            dropped += 1
            continue
        digest = _actor_hash(actor)
        ts = _ts(item.get("createdAt"))
        attrs: dict[str, Any] = {"pr": pr_ref}
        # GitHub emits one ReviewRequestedEvent per reviewer, all sharing the
        # requester and the same createdAt. The reviewer must therefore be part
        # of the entity id or a batch request of five collapses to one event.
        suffix = digest or "anon"
        if activity == "review_requested":
            reviewer = item.get("requestedReviewer") or {}
            requested = _actor_hash(reviewer) or (
                f"team:{reviewer['slug']}" if reviewer.get("slug") else None
            )
            attrs["requested_reviewer"] = requested
            suffix = f"{suffix}:{requested or 'unknown'}"
        add(activity, ts, f"{pr_ref}:{activity}:{suffix}", digest, attrs)

    # --- the merge itself. No actor: see the module docstring.
    add("merged", merged, pr_ref, None, {"pr": pr_ref, "actor_basis": "not_fetched"})

    if stats is not None:
        stats.bot_events_dropped += dropped
        stats.unmapped_review_states.update(unmapped)
        if work_item["epic"]:
            stats.epics_found += 1

    merge_commit = (body.get("mergeCommit") or {}).get("oid")
    return MappedPR(
        work_item=work_item,
        events=events,
        merge_commit_sha=str(merge_commit) if merge_commit else None,
        case_source=case_source,
        bot_events_dropped=dropped,
        unmapped_review_states=unmapped,
    )


def map_workflow_run(
    body: dict[str, Any], case_by_sha: dict[str, str]
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """One workflow run -> a `ci_run` row, plus an event when it has a case.

    Returns (ci_run_row, event_row_or_None). The run row is always produced:
    an unmatched run is real spend even when we cannot attribute it, and the
    `ci_run` table's `work_item_id` is nullable for exactly that.
    """
    repo = str(body.get("repo") or "")
    head_sha = body.get("head_sha")
    head_sha = str(head_sha) if head_sha else None
    attempt = int(body.get("run_attempt") or 1)
    run_id = f"{repo}:{body['id']}:{attempt}"

    started = _ts(body.get("run_started_at")) or _ts(body.get("updated_at"))
    finished = _ts(body.get("updated_at"))
    minutes = 0.0
    if started and finished and finished > started:
        minutes = (finished - started).total_seconds() / 60.0

    work_item_id = case_by_sha.get(head_sha) if head_sha else None

    ci_row = {
        "run_id": run_id,
        "work_item_id": work_item_id,
        "repo": repo,
        "head_sha": head_sha,
        "ts": started,
        "runner_minutes": round(minutes, 3),
        "conclusion": body.get("conclusion"),
        "attempt": attempt,
    }

    event = None
    if work_item_id and started:
        event = {
            "event_id": event_id_for(
                ACTIONS_SOURCE, RUN_ENTITY, run_id, "ci_run", started
            ),
            "work_item_id": work_item_id,
            # No human behind a CI run. The column is nullable for this.
            "actor_hash": None,
            "activity": "ci_run",
            "ts": started,
            "source": EVENT_SOURCE,
            "attrs": {
                "run_id": run_id,
                "head_sha": head_sha,
                "conclusion": body.get("conclusion"),
                "attempt": attempt,
                "runner_minutes_basis": "wall_clock",
            },
        }
    return ci_row, event


def plan_repointing(
    commit_rows: Sequence[tuple[str, str, str]],
    case_by_sha: dict[str, str],
    repo_by_case: dict[str, str],
) -> tuple[list[dict[str, str]], int]:
    """Decide which commit events move to the PR's case. Pure.

    `commit_rows` is (event_id, work_item_id, sha). Returns the update params
    and a count of commits deliberately left where they were.

    Three rules, in order:

    1. Current case is git_local's provisional `{repo}@{sha[:12]}` -> always
       re-point. That id exists only because nothing better was known yet.
    2. Current case is the `{repo}#{number}` form and the PR resolved to a
       ticket key -> re-point, merging the PR-shaped case into the ticket. Not
       doing this is what leaves coding and reviewing as two disconnected
       islands in the process graph.
    3. Current case is already a ticket key -> leave it. git_local's anchored
       regex on the commit subject is a strong signal and a PR body mentioning
       a different key must not override it.
    """
    updates: list[dict[str, str]] = []
    left = 0
    for event_id, current, sha in commit_rows:
        target = case_by_sha.get(sha)
        if target is None or target == current:
            continue
        repo = repo_by_case.get(target, "")
        provisional = repo and current.startswith(f"{repo}@")
        pr_shaped = current.startswith(f"{repo}#") if repo else "#" in current
        if provisional or (pr_shaped and "#" not in target):
            updates.append({"eid": event_id, "wid": target})
        else:
            left += 1
    return updates, left


# =========================================================================
# Database
# =========================================================================


def load_payloads(
    session: Session, repos: Sequence[str] | None = None
) -> dict[str, list[dict[str, Any]]]:
    """Read the GitHub raw payloads. `asf_jira` is excluded by the WHERE clause."""
    rows = session.execute(
        select(RawPayload.source, RawPayload.body).where(
            RawPayload.source.in_(GITHUB_SOURCES)
        )
    ).all()
    out: dict[str, list[dict[str, Any]]] = {s: [] for s in GITHUB_SOURCES}
    for source, body in rows:
        if repos and body.get("repo") not in repos:
            continue
        out.setdefault(source, []).append(body)
    return out


def _write_work_items(session: Session, rows: list[dict[str, Any]]) -> int:
    """Upsert cases, updating only the columns a PR actually owns.

    `component` is COALESCEd rather than overwritten: git_local's comment says
    git is authoritative for it, since it derives from file paths. Overwriting
    would make the stored value depend on which mapper ran last, and
    `v_spend_by_component` groups by it. A PR fills the gap for a case with no
    in-window commit and otherwise defers.

    `jira_key` is likewise never blanked, and `sprint` is not in the update set
    at all — see the module docstring.
    """
    if not rows:
        return 0
    assert_no_identity(rows)
    stmt = insert(WorkItem).values(rows)
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["work_item_id"],
            set_={
                "component": func.coalesce(
                    WorkItem.component, stmt.excluded.component
                ),
                "jira_key": func.coalesce(WorkItem.jira_key, stmt.excluded.jira_key),
                "epic": func.coalesce(WorkItem.epic, stmt.excluded.epic),
                "closed_at": stmt.excluded.closed_at,
                "source_ref": stmt.excluded.source_ref,
            },
        )
    )
    return len(rows)


def _write_actors(session: Session, events: list[dict[str, Any]]) -> int:
    """Create `actor` rows for hashes seen here but not yet in the table.

    No identity store call and no login: the connector hashed every login at
    the fetch boundary, so this module never handles one. `role_band` is the
    same provisional rule git_local uses, over events observed here rather than
    commits, and `band_basis` stays 'inferred' as it must (decision #8).

    `on_conflict_do_nothing` means a git-derived actor keeps its git-derived
    band; a reviewer who never commits gets one from their review volume.
    """
    seen: dict[str, list[datetime]] = {}
    for event in events:
        digest = event.get("actor_hash")
        if digest:
            seen.setdefault(digest, []).append(event["ts"])
    if not seen:
        return 0

    rows = [
        {
            "actor_hash": digest,
            "role_band": infer_band(len(stamps)),
            "tenure_bucket": infer_tenure(min(stamps), max(stamps)),
            "first_seen": min(stamps),
            "band_basis": "inferred",
        }
        for digest, stamps in seen.items()
    ]
    assert_no_identity(rows)
    stmt = insert(Actor).values(rows)
    session.execute(stmt.on_conflict_do_nothing(index_elements=["actor_hash"]))
    return len(rows)


def _write_events(session: Session, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    assert_no_identity(rows)
    # Deduplicate within the batch: Postgres rejects an ON CONFLICT statement
    # whose own VALUES hit the same key twice.
    unique = {row["event_id"]: row for row in rows}
    stmt = insert(EventLog).values(list(unique.values()))
    session.execute(stmt.on_conflict_do_nothing(index_elements=["event_id"]))
    return len(unique)


def _write_ci_runs(session: Session, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    assert_no_identity(rows)
    unique = {row["run_id"]: row for row in rows}
    stmt = insert(CiRun).values(list(unique.values()))
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["run_id"],
            set_={
                "work_item_id": stmt.excluded.work_item_id,
                "conclusion": stmt.excluded.conclusion,
                "runner_minutes": stmt.excluded.runner_minutes,
            },
        )
    )
    return len(unique)


def _load_commit_events(session: Session) -> list[tuple[str, str, str]]:
    """(event_id, work_item_id, sha) for every commit event already mapped."""
    rows = session.execute(
        select(EventLog.event_id, EventLog.work_item_id, EventLog.attrs).where(
            EventLog.activity == "commit"
        )
    ).all()
    out = []
    for event_id, work_item_id, attrs in rows:
        sha = (attrs or {}).get("sha")
        if sha:
            out.append((event_id, work_item_id, str(sha)))
    return out


def _apply_repointing(session: Session, updates: list[dict[str, str]]) -> int:
    if not updates:
        return 0
    session.execute(
        EventLog.__table__.update()
        .where(EventLog.event_id == bindparam("eid"))
        .values(work_item_id=bindparam("wid")),
        updates,
    )
    return len(updates)


# =========================================================================
# Orchestration
# =========================================================================


def run(
    session: Session, repos: Sequence[str] | None = None, stats: Stats | None = None
) -> Stats:
    stats = stats or Stats()
    payloads = load_payloads(session, repos)

    stats.git_local_skipped = len(payloads.get(GIT_SOURCE, []))
    stats.payloads_read = sum(len(v) for v in payloads.values())

    mapped = [map_pull_request(body, stats) for body in payloads.get(PR_SOURCE, [])]
    stats.pull_requests = len(mapped)

    work_items = [m.work_item for m in mapped]
    events = [e for m in mapped for e in m.events]
    for m in mapped:
        stats.case_sources[m.case_source] += 1
    for event in events:
        stats.activities[event["activity"]] += 1

    # Cases must exist before events and ci_runs can reference them.
    stats.work_items = _write_work_items(session, work_items)
    session.flush()
    stats.actors = _write_actors(session, events)
    session.flush()

    # Re-point commits before writing PR events, so both land on one case.
    case_by_sha = {
        m.merge_commit_sha: m.work_item["work_item_id"]
        for m in mapped
        if m.merge_commit_sha
    }
    repo_by_case = {m.work_item["work_item_id"]: m.work_item["repo"] for m in mapped}
    updates, left = plan_repointing(
        _load_commit_events(session), case_by_sha, repo_by_case
    )
    stats.commits_repointed = _apply_repointing(session, updates)
    stats.commits_left_alone = left

    stats.events = _write_events(session, events)

    # --- workflow runs. Match on head_sha against every commit we know.
    sha_to_case = dict(case_by_sha)
    for _event_id, work_item_id, sha in _load_commit_events(session):
        sha_to_case.setdefault(sha, work_item_id)

    ci_rows: list[dict[str, Any]] = []
    run_events: list[dict[str, Any]] = []
    for body in payloads.get(ACTIONS_SOURCE, []):
        ci_row, event = map_workflow_run(body, sha_to_case)
        ci_rows.append(ci_row)
        if event:
            run_events.append(event)
            stats.runs_matched += 1
        else:
            stats.runs_unmatched += 1
    stats.workflow_runs = len(ci_rows)
    stats.ci_runs = _write_ci_runs(session, ci_rows)
    stats.events += _write_events(session, run_events)
    for event in run_events:
        stats.activities[event["activity"]] += 1

    session.flush()
    stats.sprintless_cases = int(
        session.execute(
            select(func.count()).select_from(WorkItem).where(WorkItem.sprint.is_(None))
        ).scalar_one()
    )
    stats.orphaned_provisional = _count_orphaned_cases(session)
    return stats


def _count_orphaned_cases(session: Session) -> int:
    """Cases left with no events after re-pointing.

    Nothing is deleted: dropping a row because it became empty is how a
    reconciliation bug turns into missing data nobody can find later. They are
    counted and reported so the decision stays a human one.
    """
    return int(
        session.execute(
            select(func.count())
            .select_from(WorkItem)
            .where(
                ~select(EventLog.event_id)
                .where(EventLog.work_item_id == WorkItem.work_item_id)
                .exists()
            )
        ).scalar_one()
    )


def _print_report(stats: Stats, dry_run: bool) -> None:
    # ASCII only: the team is on Windows terminals whose default code page
    # turns an em dash into a replacement character.
    print("\n" + "=" * 70)
    print("GITHUB MAPPING" + ("  [DRY RUN - rolled back]" if dry_run else ""))
    print("=" * 70)

    print("\n  raw_payload read (github sources only, never asf_jira)")
    print(f"    total rows              {stats.payloads_read}")
    print(f"    git_local (already mapped by the ingester, skipped)  "
          f"{stats.git_local_skipped}")
    print(f"    pull requests mapped    {stats.pull_requests}")
    print(f"    workflow runs mapped    {stats.workflow_runs}")

    print("\n  rows written")
    print(f"    work_item               {stats.work_items}")
    print(f"    event_log               {stats.events}")
    print(f"    ci_run                  {stats.ci_runs}")
    print(f"    actor                   {stats.actors}")

    print("\n  case_source breakdown (decision #6 fallback chain)")
    total = sum(stats.case_sources.values()) or 1
    for source in ("ticket_key", "issue", "pr"):
        n = stats.case_sources.get(source, 0)
        print(f"    {source:<22} {n:>6}  {n / total:6.1%}")

    if stats.activities:
        print("\n  events by activity")
        for activity, n in stats.activities.most_common():
            print(f"    {activity:<22} {n:>6}")

    print("\n  commit -> PR reconciliation")
    print(f"    commits re-pointed      {stats.commits_repointed}")
    print(f"    left on their own case  {stats.commits_left_alone}")
    print(f"    CI runs matched by sha  {stats.runs_matched}")
    print(f"    CI runs unattributed    {stats.runs_unmatched}  "
          "(ci_run.work_item_id null, by design)")

    print("\n  things to know")
    print(f"    bot events dropped      {stats.bot_events_dropped}")
    print(f"    epics resolved          {stats.epics_found}"
          "   (needs milestone/labels in PR_QUERY)")
    if stats.unmapped_review_states:
        states = ", ".join(
            f"{state}={n}" for state, n in stats.unmapped_review_states.most_common()
        )
        print(f"    review states skipped   {states}  (not in the vocabulary)")
    print(f"    cases with no events    {stats.orphaned_provisional}  "
          "(kept, never deleted)")
    if stats.sprintless_cases:
        print(f"\n    {stats.sprintless_cases} case(s) have a NULL sprint.")
        print("    sprint is read here, never computed (decision #7). Re-derive")
        print("    the global grid with --assign-sprints, which calls")
        print("    git_local.assign_sprints rather than reimplementing it.")
    print()


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Map GitHub raw_payload rows.")
    parser.add_argument("--repo", action="append", help="owner/name; repeatable")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="map everything, print the report, then roll back",
    )
    parser.add_argument(
        "--assign-sprints",
        action="store_true",
        help="after mapping, re-derive the global sprint grid by calling "
        "git_local.assign_sprints. Off by default: sprint is read here, "
        "never computed.",
    )
    args = parser.parse_args(argv)

    stats = Stats()
    if args.dry_run:
        from app.db.session import get_write_engine

        with Session(get_write_engine()) as session:
            stats = run(session, args.repo, stats)
            session.rollback()
    else:
        with write_session() as session:
            stats = run(session, args.repo, stats)
            if args.assign_sprints:
                from app.config import get_settings
                from app.ingestion.git_local import assign_sprints

                windows = assign_sprints(session, get_settings().sprint_days)
                logger.info("re-derived the global sprint grid: %d windows", windows)

    _print_report(stats, args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
