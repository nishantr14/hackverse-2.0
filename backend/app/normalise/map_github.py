"""
GitHub mapping — raw_payload -> work_item, event_log, ci_run. NO NETWORK.
Owner: Dipen (normalise lane).
Phase: Tier 0.

*** NOT THE CANONICAL PIPELINE — DO NOT RUN AGAINST THE SHARED DATABASE. ***
`app.normalise.event_log` (see README's "Building the canonical event log")
is what actually built the data in this project's Postgres, is what
`.claude/CLAUDE.md`/README document, and is what everything else reads.
This module is a second, independent implementation of overlapping PR
mapping that nothing else imports. Its event_id formula for reviews and
timeline events differs from event_log.py's (digest embedded in the entity
id here vs. a trailing hash component there), so running it against a
database event_log.py has already populated does NOT converge via the usual
on_conflict_do_nothing upsert — it silently writes a second, duplicate copy
of every review/approved/changes_requested/review_requested/reopened event
under different ids. This happened for real on 2026-08-09 (38,723 duplicate
rows, since cleaned up) while porting P2's commit-mapping fix — see that
commit's message. Until this module is either wired into the real pipeline
or retired, treat it as a reference/prototype only.

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
from datetime import UTC, datetime, timedelta
from typing import Any

from app.config import get_settings
from app.db.models import Actor, CiRun, EventLog, RawPayload, WorkItem
from app.db.session import write_session
from app.ingestion.git_local import ROOT_COMPONENT, infer_band, infer_tenure
from app.ingestion.projects import is_real_ticket
from app.ingestion.pseudonymize import assert_no_identity
from app.normalise.case_span import CaseSpan, SpanReport, span_days, summarise
from app.normalise.event_log import absence_reason
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


#: Postgres' wire protocol caps one statement at 65,535 bound parameters, and
#: a full kafka window is ~20,000 events times 7 columns = ~140,000. Every bulk
#: insert below is therefore chunked. The chunk size is derived from the row
#: width at call time rather than hardcoded, so adding a column to a table
#: cannot silently push a batch back over the limit.
PG_MAX_BIND_PARAMS = 65535


#: Days per month, as both ingesters compute it. Restated rather than imported
#: because neither exposes it, but the arithmetic must stay identical: a
#: cutoff half a day from git_local's would put commits and PRs in different
#: windows and reintroduce exactly the drift this filter removes.
DAYS_PER_MONTH = 30.44


def window_cutoff(now: datetime | None = None) -> datetime:
    """The HISTORY_MONTHS boundary, computed exactly as the ingesters do.

    `github_connector` pages by UPDATED_AT DESC and stops at this boundary,
    which is the right filter for "was this active recently" and the wrong one
    for "did this happen recently". A 2023 PR re-enters the feed the moment
    somebody comments on it, carrying its 2023 createdAt. The connector's own
    `_content_predates_window` docstring says so and deliberately does not drop
    the row - it counts it and leaves the decision to this module, because
    `raw_payload` is supposed to keep everything that was fetched.

    This is that decision. `now` is injectable so tests do not depend on the
    wall clock; production passes nothing, matching git_local:614 and
    github_connector:606.
    """
    months = get_settings().history_months
    return (now or datetime.now(UTC)) - timedelta(days=round(months * DAYS_PER_MONTH))


def _chunked(
    rows: list[dict[str, Any]], width: int
) -> Iterable[list[dict[str, Any]]]:
    size = max(1, PG_MAX_BIND_PARAMS // max(width, 1))
    for start in range(0, len(rows), size):
        yield rows[start : start + size]


@dataclass
class Stats:
    payloads_read: int = 0
    git_local_skipped: int = 0
    pull_requests: int = 0
    #: PRs both created and merged before the HISTORY_MONTHS boundary.
    #: Skipped, never deleted - the raw layer keeps what it fetched.
    prs_outside_window: int = 0
    #: PRs created before the boundary but merged or still open inside it.
    #: Kept. Counted because filtering on createdAt alone would drop them.
    prs_created_before_window_kept: int = 0
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
    cases_merged: int = 0
    prs_merged_away: int = 0
    events_collapsed: int = 0
    ci_runs_collapsed: int = 0
    largest_case: tuple[str, int] | None = None
    #: Umbrella cases, computed at read time from rows this run produced.
    #: Advisory only - decision #6 is unchanged and nothing is stored.
    spans: SpanReport | None = None


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
    # `[A-Z]{2,10}-\d+` matches far more than Jira keys — KIP-909, CVE-2026,
    # SHA-256, GPT-5 all match. is_real_ticket requires the project prefix to
    # be the one this repo's Jira actually uses; the same guard git_local
    # applies to a commit subject, applied here to a PR title/branch/body.
    if key and is_real_ticket(key, repo):
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
    #: Files touched, kept so `merge_work_items` can weight a component vote by
    #: how much of the case each PR actually changed.
    paths: list[str] = field(default_factory=list)
    pr_number: int = 0
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
    merge_commit = (body.get("mergeCommit") or {}).get("oid")

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

    # --- the PR's own commits (P2: squash-merge collapses trunk history to
    # one commit; these are what session inference actually clusters on).
    #
    # event_id is byte-identical to git_local.event_id_for on the same
    # (sha, authored_at) — not a coincidence, see event_id_for's docstring.
    # A commit git_local already saw on trunk (a non-squash merge, or a PR
    # whose branch commits survived) converges on that same id and the
    # upsert's on_conflict_do_nothing leaves git_local's row untouched: no
    # double-count. A commit squashed away before it ever reached trunk has
    # no existing row to collide with and lands here for the first time.
    for commit_node in _nodes(body.get("commits")):
        commit = commit_node.get("commit") or {}
        oid = commit.get("oid")
        authored = _ts(commit.get("authoredDate"))
        if not oid or authored is None:
            continue
        author = (commit.get("author") or {}).get("user")
        if _is_bot(author):
            dropped += 1
            continue
        attrs: dict[str, Any] = {
            "sha": oid,
            "pr": pr_ref,
            "additions": commit.get("additions"),
            "deletions": commit.get("deletions"),
            "changed_files": commit.get("changedFiles"),
            "is_squash_merge": bool(merge_commit) and oid == merge_commit,
        }
        # No linked GitHub account for this commit's author (deleted account,
        # or a git email that never mapped to a login) — a null resource is
        # fine, an unexplained one is what a mapper that dropped a human looks
        # like. See event_log.absence_reason.
        reason = absence_reason(author)
        if reason:
            attrs["actor_absent"] = reason
        events.append(
            {
                "event_id": event_id_for(
                    GIT_SOURCE, "commit", str(oid), "commit", authored
                ),
                "work_item_id": work_item_id,
                "actor_hash": _actor_hash(author),
                "activity": "commit",
                "ts": authored,
                "source": EVENT_SOURCE,
                "attrs": attrs,
            }
        )

    if stats is not None:
        stats.bot_events_dropped += dropped
        stats.unmapped_review_states.update(unmapped)
        if work_item["epic"]:
            stats.epics_found += 1

    return MappedPR(
        work_item=work_item,
        events=events,
        merge_commit_sha=str(merge_commit) if merge_commit else None,
        case_source=case_source,
        paths=paths,
        pr_number=int(number),
        bot_events_dropped=dropped,
        unmapped_review_states=unmapped,
    )


# =========================================================================
# Case merging — several PRs on one case
# =========================================================================

#: Decision #6's chain, strongest first. Used to pick a winner when PRs that
#: share a case resolved their id by different routes.
CASE_SOURCE_PRECEDENCE: tuple[str, ...] = ("ticket_key", "issue", "pr")


def merge_work_items(group: Sequence[MappedPR]) -> dict[str, Any]:
    """Collapse every PR sharing one work_item_id into a single row.

    Apache files a main PR, its follow-ups and its backports under one Jira
    key, so `KAFKA-10199` is 19 pull requests. Sending 19 rows with the same
    primary key into one `INSERT ... ON CONFLICT DO UPDATE` is what Postgres
    rejects with CardinalityViolation: within a single statement it refuses to
    update the same row twice, because which of the 19 won would be arbitrary.

    Every rule below is a function of the group's contents only, never of the
    order the payloads came back in, so two runs over the same data produce
    byte-identical rows:

    opened_at   MIN over the group. The case started when its first PR opened.
    closed_at   MAX over the group, but NULL if any member is still open — a
                case with live work in it is not closed, and taking the MAX
                would report it finished while a PR is still in review.
    component   `component_of` over every path in the group, so the vote is
                weighted by how many files each PR touched and the existing
                alphabetical tie-break still applies. Falls back to a vote
                over the members' own components when no PR listed files.
    case_source strongest present, ticket_key > issue > pr.
    source_ref  the earliest-opened PR, tie-broken by the lower number.
    epic        most common non-null, tie-broken alphabetically.
    """
    if not group:
        raise ValueError("cannot merge an empty group")
    items = [m.work_item for m in group]
    ids = {item["work_item_id"] for item in items}
    if len(ids) != 1:
        raise ValueError(f"merge_work_items got mixed ids: {sorted(ids)}")

    opened = [item["opened_at"] for item in items if item["opened_at"]]
    closed = [item["closed_at"] for item in items]

    paths = [path for m in group for path in m.paths]
    component = component_of(paths)
    if component is None:
        component = _most_common(item["component"] for item in items)

    # min() over (opened_at, number) needs a total order, and opened_at may be
    # None. Sort None last so a PR with a real date always wins the tie.
    lead = min(
        group,
        key=lambda m: (
            m.work_item["opened_at"] is None,
            m.work_item["opened_at"] or datetime.max.replace(tzinfo=UTC),
            m.pr_number,
        ),
    )

    return {
        "work_item_id": items[0]["work_item_id"],
        "repo": min(item["repo"] for item in items),
        "component": component,
        "epic": _most_common(item["epic"] for item in items),
        "opened_at": min(opened) if opened else None,
        # `all(closed)` is deliberate: one still-open PR keeps the case open.
        "closed_at": max(closed) if closed and all(closed) else None,
        "source_ref": lead.work_item["source_ref"],
        "case_source": _strongest_case_source(m.case_source for m in group),
        "jira_key": _most_common(item["jira_key"] for item in items),
    }


def _most_common(values: Iterable[Any]) -> Any:
    """Most frequent non-null value, ties broken alphabetically.

    The tie-break is what makes this run-order independent: `Counter.most_common`
    alone falls back to insertion order, which is exactly the dependency this
    function exists to remove.
    """
    counts = Counter(v for v in values if v)
    if not counts:
        return None
    best = max(counts.values())
    return min(v for v, n in counts.items() if n == best)


def _strongest_case_source(values: Iterable[str]) -> str:
    present = set(values)
    for candidate in CASE_SOURCE_PRECEDENCE:
        if candidate in present:
            return candidate
    return "pr"


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

    `opened_at` is LEAST, not overwrite, matching git_local's own rule: a case
    opened at the earliest of its first commit and its first PR, whichever run
    discovered it. Leaving it out of the update set entirely — which is what
    this function did until the 0.00-day median exposed it — meant a case
    git_local had already created kept the merge commit's author date as its
    opened_at while `closed_at` was overwritten with the PR's. For a squash
    merge those two are the same instant, which is why 713 cases stored a span
    of exactly zero while the mapper itself computed none.

    Callers must have merged duplicates already. The guard below turns what
    Postgres reports as an opaque "ON CONFLICT DO UPDATE command cannot affect
    row a second time" into a message naming the offending case ids.
    """
    if not rows:
        return 0
    duplicates = [
        wid
        for wid, n in Counter(row["work_item_id"] for row in rows).items()
        if n > 1
    ]
    if duplicates:
        raise ValueError(
            f"{len(duplicates)} duplicate work_item_id(s) in one INSERT batch, "
            f"e.g. {sorted(duplicates)[:5]}. Postgres cannot update the same "
            "row twice in one statement — merge the group with "
            "merge_work_items() before calling this."
        )
    assert_no_identity(rows)
    for chunk in _chunked(rows, len(rows[0])):
        stmt = insert(WorkItem).values(chunk)
        session.execute(
            stmt.on_conflict_do_update(
                index_elements=["work_item_id"],
                set_={
                    "component": func.coalesce(
                        WorkItem.component, stmt.excluded.component
                    ),
                    "jira_key": func.coalesce(
                        WorkItem.jira_key, stmt.excluded.jira_key
                    ),
                    "epic": func.coalesce(WorkItem.epic, stmt.excluded.epic),
                    # LEAST ignores nulls in Postgres, so a case with only one
                    # of the two dates keeps it.
                    "opened_at": func.least(
                        WorkItem.opened_at, stmt.excluded.opened_at
                    ),
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
    for chunk in _chunked(rows, len(rows[0])):
        stmt = insert(Actor).values(chunk)
        session.execute(stmt.on_conflict_do_nothing(index_elements=["actor_hash"]))
    return len(rows)


def _write_events(session: Session, rows: list[dict[str, Any]]) -> tuple[int, int]:
    """Write events. Returns (written, collapsed).

    An event_id collides only when one actor performs the same activity on the
    same PR in the same second. Measured on apache/kafka that is 5 rows in
    20,112, all of them one reviewer submitting a batch of review comments,
    which GitHub records as several `reviews` nodes sharing a `submittedAt`.

    Collapsing them is the right model — one human action at one instant is one
    event — but the count is RETURNED rather than swallowed, because a silent
    dedup is indistinguishable from a bug that quietly eats real events.
    """
    if not rows:
        return 0, 0
    assert_no_identity(rows)
    unique = list({row["event_id"]: row for row in rows}.values())
    for chunk in _chunked(unique, len(unique[0])):
        stmt = insert(EventLog).values(chunk)
        session.execute(stmt.on_conflict_do_nothing(index_elements=["event_id"]))
    return len(unique), len(rows) - len(unique)


def _write_ci_runs(session: Session, rows: list[dict[str, Any]]) -> tuple[int, int]:
    """Write CI runs. Returns (written, collapsed).

    `run_id` is `{repo}:{id}:{attempt}`, so a collision means the same attempt
    of the same run appeared twice in one batch. The winner is the observation
    with the most wall-clock time on it rather than whichever happened to be
    last in the list: a run seen twice is usually seen once mid-flight and once
    finished, and the finished view is the complete one.
    """
    if not rows:
        return 0, 0
    assert_no_identity(rows)
    unique: dict[str, dict[str, Any]] = {}
    for row in rows:
        current = unique.get(row["run_id"])
        if current is None or row["runner_minutes"] > current["runner_minutes"]:
            unique[row["run_id"]] = row
    winners = list(unique.values())
    for chunk in _chunked(winners, len(winners[0])):
        stmt = insert(CiRun).values(chunk)
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
    return len(winners), len(rows) - len(winners)


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


def _within_window(
    bodies: list[dict[str, Any]], stats: Stats, now: datetime | None = None
) -> list[dict[str, Any]]:
    """Drop PRs opened before the HISTORY_MONTHS boundary. Counts, never deletes.

    Measured on apache/kafka: 529 of 3,314 landed PRs were opened outside the
    window, the oldest `apache/kafka#1` from 2012. They are in `raw_payload`
    because the connector pages by UPDATED_AT and someone commented on them
    recently - not because the work happened recently. Mapping them:

      * stretched the global sprint grid from ~26 windows to 289, since
        `assign_sprints` numbers backwards from the newest event to the oldest;
      * produced 177 of check 2's 239 "merged with no commit" cases, because
        git_local only clones 12 months so the commit can never be there;
      * put 2015 timestamps in a log the UI labels a 12-month window.

    THE TEST IS `mergedAt or createdAt`, NOT `createdAt`. That is
    `github_connector._content_predates_window`'s own rule, restated here so
    the fetch boundary and the mapping boundary cannot disagree about which
    PRs belong to the window.

    Filtering on `createdAt` alone was the first version of this function and
    it was too aggressive: Nishant measured 788 PRs against the live API that
    were created before the boundary but are still active or completed inside
    it, carrying 1,403 in-window events and 127 merges. A PR opened in 2023 and
    merged last month is in-window work, whatever its creation date says.

    What still goes, correctly: a PR both created and merged before the
    boundary, in `raw_payload` only because someone commented on it recently.
    Mapping those stretched the global sprint grid from ~26 windows to 289 and
    produced 177 of check 2's 239 "merged with no commit" cases, since
    git_local only clones 12 months so the commit cannot be there.

    Both counts are reported: what was skipped, and how many PRs older than the
    boundary this rule keeps that `createdAt` alone would have dropped.
    """
    cutoff = window_cutoff(now)
    kept: list[dict[str, Any]] = []
    for body in bodies:
        created = _ts(body.get("createdAt"))
        # mergedAt first: it is the stamp that says when the work landed.
        # Falling back to createdAt keeps an unmerged PR judged on the only
        # date it has, which is what the connector does.
        stamp = _ts(body.get("mergedAt")) or created
        if stamp is not None and stamp < cutoff:
            stats.prs_outside_window += 1
            continue
        if created is not None and created < cutoff:
            stats.prs_created_before_window_kept += 1
        kept.append(body)
    return kept


def run(
    session: Session, repos: Sequence[str] | None = None, stats: Stats | None = None
) -> Stats:
    stats = stats or Stats()
    payloads = load_payloads(session, repos)

    stats.git_local_skipped = len(payloads.get(GIT_SOURCE, []))
    stats.payloads_read = sum(len(v) for v in payloads.values())

    mapped = [
        map_pull_request(body, stats)
        for body in _within_window(payloads.get(PR_SOURCE, []), stats)
    ]
    stats.pull_requests = len(mapped)

    # Several PRs routinely share one case: Apache files a main PR, its
    # follow-ups and its backports under one Jira key. Merge them BEFORE the
    # insert — one row per case is both what Postgres requires and what the
    # case actually is.
    by_case: dict[str, list[MappedPR]] = {}
    for m in mapped:
        by_case.setdefault(m.work_item["work_item_id"], []).append(m)

    work_items = [merge_work_items(group) for group in by_case.values()]
    events = [e for m in mapped for e in m.events]

    stats.cases_merged = sum(1 for group in by_case.values() if len(group) > 1)
    stats.prs_merged_away = len(mapped) - len(by_case)
    if by_case:
        widest = max(by_case.items(), key=lambda kv: len(kv[1]))
        stats.largest_case = (widest[0], len(widest[1]))

    # Umbrella detection reads the merged rows and writes nothing (see
    # app/normalise/case_span.py). It runs on what this pass produced, not on
    # the whole table, so the report describes this run.
    stats.spans = summarise(
        [
            CaseSpan(
                work_item_id=row["work_item_id"],
                days=span_days(row["opened_at"], row["closed_at"]),
                n_prs=len(by_case[row["work_item_id"]]),
                case_source=row["case_source"],
            )
            for row in work_items
        ]
    )
    for row in work_items:
        stats.case_sources[row["case_source"]] += 1
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

    stats.events, stats.events_collapsed = _write_events(session, events)

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
    stats.ci_runs, stats.ci_runs_collapsed = _write_ci_runs(session, ci_rows)
    written, collapsed = _write_events(session, run_events)
    stats.events += written
    stats.events_collapsed += collapsed
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


UMBRELLA_TOP_N = 10


def _print_umbrellas(report: SpanReport | None) -> None:
    """The `is_umbrella` section. Advisory - it changes no row and no rule.

    Printed right under case merging because that is what produces the shape:
    merging is correct per decision #6, and this says which of its results are
    work programmes rather than units of work.
    """
    if report is None or report.n_cases == 0:
        return

    print("\n  umbrella cases (computed at read time - NOT a stored column)")
    print(f"    rule                    span > {report.multiple:g}x median "
          f"OR >= {report.min_prs} PRs")
    if report.median_days is None:
        print("    median case span        n/a - no case has both dates yet")
    else:
        print(f"    median case span        {report.median_days:.2f} days  "
              f"(over {report.n_measurable} closed case(s))")
    if report.unscalable:
        print("    span threshold          n/a - nothing to scale, so the span")
        print("                            half of the rule did not fire")
    else:
        print(f"    span threshold          {report.threshold_days:.1f} days")

    share = len(report.umbrellas) / report.n_cases
    print(f"    is_umbrella = true      {len(report.umbrellas)} of "
          f"{report.n_cases} cases  ({share:.1%})")
    print(f"      by span               {len(report.over_span)}")
    print(f"      by PR count           {len(report.over_pr_count)}")

    if report.umbrellas:
        print(f"\n    top {min(UMBRELLA_TOP_N, len(report.umbrellas))} by span")
        print(f"      {'case':<24} {'span_days':>10}  {'prs':>4}  {'case_source':<12}")
        for case in report.top(UMBRELLA_TOP_N):
            days = "open" if case.days is None else f"{case.days:.1f}"
            print(f"      {case.work_item_id[:24]:<24} {days:>10}  "
                  f"{case.n_prs or 0:>4}  {case.case_source or '':<12}")
        print("\n    Decision #6 stands - each of these is still one case. The flag")
        print("    is advisory: cycle-time and cost charts should be able to say")
        print("    which cases are work programmes rather than units of work.")


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

    if stats.prs_outside_window or stats.prs_created_before_window_kept:
        months = get_settings().history_months
        print(f"\n  the {months}-month window (rule: mergedAt or createdAt,")
        print("  matching github_connector._content_predates_window)")
        print(f"    PRs skipped             {stats.prs_outside_window}  "
              "(created AND merged before it)")
        print(f"    older PRs kept          {stats.prs_created_before_window_kept}  "
              "(created before it, landed inside)")
        print("    Skipped rows stay in raw_payload - the raw layer keeps")
        print("    everything it fetched. They are only there because the")
        print("    connector pages by UPDATED_AT, and git_local clones 12")
        print("    months, so their commits cannot exist either way.")

    print("\n  case merging (several PRs on one ticket key)")
    print(f"    cases with >1 PR        {stats.cases_merged}")
    print(f"    PRs folded into them    {stats.prs_merged_away}")
    if stats.largest_case:
        case_id, n = stats.largest_case
        print(f"    largest case            {case_id} ({n} PRs)")
    print("    full PR set per case is recoverable from event_log.attrs->>'pr';")
    print("    work_item has no column for a list - see the run notes.")

    _print_umbrellas(stats.spans)

    print("\n  rows written")
    print(f"    work_item               {stats.work_items}")
    print(f"    event_log               {stats.events}")
    print(f"    ci_run                  {stats.ci_runs}")
    print(f"    actor                   {stats.actors}")
    if stats.events_collapsed or stats.ci_runs_collapsed:
        print(f"    events collapsed        {stats.events_collapsed}  "
              "(one actor, same activity, same second)")
        print(f"    ci_runs collapsed       {stats.ci_runs_collapsed}")

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
