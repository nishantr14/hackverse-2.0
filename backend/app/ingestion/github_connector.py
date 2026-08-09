"""
GitHub connector — pull requests (GraphQL) and Actions runs (REST).
Owner: Nishant (ingestion lane).
Phase: Tier 0.

    python -m app.ingestion.github_connector --repo apache/kafka
    python -m app.ingestion.github_connector --repo apache/kafka --actions
    python -m app.ingestion.github_connector --repo apache/kafka --prs --actions

Writes `raw_payload`, `ingest_cursor`, `ci_run` and one `run_config` row.

PRs remain fetch-only: they land in `raw_payload` and Dipen maps them in
`normalise/`. Actions runs are the exception — `ci_run` is a landing table with
no interpretation in it (a run id, a sha, a duration, an attempt number), and
its only derived field is a join to `work_item` on `head_sha`. There is still
no `event_log`, `actor` or `work_item` write anywhere in this module.

ACTIONS USES REST, NOT GRAPHQL
------------------------------
Workflow runs are not exposed in GitHub's GraphQL schema at all, so this half
is REST. That means a different budget: 5,000 REQUESTS/hour, tracked with
`x-ratelimit-remaining` response headers, not the GraphQL point pool. The two
share GitHub's concurrency and abuse limits, so they run sequentially.

WALL CLOCK, NOT BILLABLE MINUTES
--------------------------------
`GET /actions/runs/{id}/timing` returns real billable minutes, but it is one
request per run — tens of thousands of requests, the entire budget, for a
number we can approximate. We use `updated_at - run_started_at` instead and
record `ci_duration_basis = wall_clock` in `run_config` so the disclosure is in
the data rather than in someone's memory of a conversation. Wall clock
OVERSTATES cost for queued runs and understates it for parallel jobs; say so
on the slide.

WHY GRAPHQL
-----------
REST needs one call for the PR, one for files, one for reviews and one for
review-request timeline events: 4-5 calls per PR. At 2,047 merged PRs in the
kafka window that is ~9,000 calls against a 5,000/hour budget — two hours and a
rate-limit wall. GraphQL returns the whole shape nested, 100 PRs per page, at a
cost of roughly 1 point per page.

IDENTITY IS HASHED AT THE FETCH BOUNDARY
----------------------------------------
The plan says to store the response in `raw_payload`, and `raw_payload` lives
in Postgres, and Postgres holds no identity (hard rule #3). Those cannot all be
true of a payload containing `author { login }`.

So every `login` is replaced by its `actor_hash` before the row is written, and
free text (`title`, `body`, `headRefName`) is redacted for @mentions and email
addresses. `_assert_scrubbed` walks the final object and raises if a single
`login` key survived. The "land raw, then map" rule still holds — Dipen's
mapper sees every field it needs and re-running it costs seconds — but the one
thing that can never be undone, identity in the warehouse, cannot happen here.
"""

from __future__ import annotations

import argparse
import logging
import random
import re
import sys
import time
from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import CiRun, EventLog, IngestCursor, RawPayload, RunConfig
from app.db.session import write_session
from app.ingestion.pseudonymize import (
    actor_hash,
    assert_no_identity,
    is_bot,
    warn_if_default_salt,
)

logger = logging.getLogger(__name__)

SOURCE = "github_graphql"
ENTITY_TYPE = "pull_request"
API_URL = "https://api.github.com/graphql"

ACTIONS_SOURCE = "github_actions"
ACTIONS_ENTITY_TYPE = "workflow_run"
REST_BASE = "https://api.github.com"

#: Fields kept from a workflow run. Everything else is discarded at the
#: boundary rather than stored and ignored — including head_commit, which
#: carries an author name and email.
RUN_FIELDS = (
    "id",
    "head_sha",
    "conclusion",
    "run_started_at",
    "updated_at",
    "run_attempt",
    "name",
)

#: run_attempt > 1 is a rerun, and reruns are the CI waste signal.
#: Recorded in run_config so the basis of every CI cost figure is IN THE DATA,
#: not in someone's memory of a conversation.
CI_DURATION_BASIS = "wall_clock"
CI_DURATION_NOTE = (
    "updated_at - run_started_at, NOT billable minutes. The per-run timing "
    "endpoint costs one request per run and would exhaust the REST budget. "
    "Wall clock overstates queued runs and understates parallel jobs."
)

#: Every GraphQL connection must be bounded 1..100. GitHub rejects the request
#: outright otherwise, and the error does not name the offending connection.
MAX_PAGE_SIZE = 100

#: Secondary limit: 2,000 points per 60 seconds. Tracked in a rolling window.
POINTS_PER_MINUTE = 2000
#: Primary limit: 5,000 points/hour. Stop and wait for reset below this.
PRIMARY_FLOOR = 50
#: Politeness gap between pages. GitHub's third secondary limit is 90 seconds
#: of server CPU per 60 seconds of real time, which no client can measure — a
#: floor on request spacing is the only lever we have against it.
MIN_REQUEST_INTERVAL_S = 0.75
MAX_RETRIES = 6

#: GitHub stops paginating actions/runs past this many results. Past it the
#: endpoint repeats rather than erroring, so we stop deliberately.
REST_PAGINATION_CAP = 1000

#: Floor for the adaptive page-size reduction in fetch_repo. Below this the
#: problem is not the page size and halving again only wastes requests.
MIN_PAGE_SIZE = 10

#: PRs per page. NOT MAX_PAGE_SIZE, and the difference is the point.
#:
#: Each PR node now carries up to 100 files + 100 reviews + 100 timeline items
#: + 20 labels + 100 commits, so 100 PRs is a lot of nodes in one response and
#: apache/kafka answered 504 every time at the old, commits-less size of 50.
#: The commits connection (added for P2: squashed merges collapse a PR's real
#: commit history to one trunk commit, leaving session inference nothing to
#: cluster) makes every page heavier again, so the starting size drops to 25.
#: The adaptive halving below still exists as the safety net, but it should
#: not be the thing that finds a working size on every single run.
PR_PAGE_SIZE = 25

#: Attempts inside execute() before fetch_repo is allowed to shrink the page.
#: A page the server cannot compute will not compute on the sixth ask either,
#: and six backoffs with full jitter is minutes of nothing. Two covers a
#: genuinely transient blip; past that, ask for less instead of asking again.
RETRIES_BEFORE_SHRINK = 2

#: "GitHub could not produce this page", in all the shapes it arrives in.
#:
#: The first four are a 200 with half a body — a transport failure wearing a
#: success code, which no status check can see. The gateway codes are the same
#: problem stated honestly: 504 is GitHub giving up on generating the
#: response. Both mean the page is too expensive, and both are cured by asking
#: for a smaller one rather than asking again. Raising reviews and
#: timelineItems to 100 produced the 504 within a page of starting.
PAGE_TOO_EXPENSIVE = (
    "incomplete chunked read",
    "without sending complete message body",
    "truncated response body",
    "peer closed connection",
    "http 502",
    "http 504",
    "timeout",
)


def _page_too_expensive(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in PAGE_TOO_EXPENSIVE)

PR_QUERY = """
query PullRequests($owner: String!, $name: String!, $cursor: String, $pageSize: Int!) {
  rateLimit { limit cost remaining resetAt nodeCount }
  repository(owner: $owner, name: $name) {
    pullRequests(
      first: $pageSize
      after: $cursor
      orderBy: { field: UPDATED_AT, direction: DESC }
      states: [OPEN, CLOSED, MERGED]
    ) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number
        title
        body
        headRefName
        createdAt
        updatedAt
        mergedAt
        closedAt
        changedFiles
        additions
        deletions
        mergeCommit { oid }
        author { login __typename }
        mergedBy { login __typename }
        milestone { title number }
        labels(first: 20) { nodes { name } }
        files(first: 100) { nodes { path } }
        reviews(first: 100) {
          nodes { state submittedAt author { login __typename } }
        }
        commits(first: 100) {
          totalCount
          nodes {
            commit {
              oid
              authoredDate
              additions
              deletions
              changedFiles
              author { user { login __typename } }
            }
          }
        }
        timelineItems(
          first: 100
          itemTypes: [REVIEW_REQUESTED_EVENT, REOPENED_EVENT, HEAD_REF_FORCE_PUSHED_EVENT]
        ) {
          nodes {
            __typename
            ... on ReviewRequestedEvent {
              createdAt
              actor { login __typename }
              requestedReviewer {
                ... on User { login }
                ... on Team { slug }
              }
            }
            ... on ReopenedEvent { createdAt actor { login __typename } }
            ... on HeadRefForcePushedEvent { createdAt actor { login __typename } }
          }
        }
      }
    }
  }
}
"""

# @mention of a GitHub login, and anything email-shaped. Both are identity and
# both appear routinely in PR bodies.
MENTION_RE = re.compile(r"(?<![\w./-])@([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))")
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")


class GitHubError(RuntimeError):
    """Non-retryable failure from the GitHub API."""


class MissingToken(GitHubError):
    """No GITHUB_TOKEN. GraphQL v4 has no anonymous tier at all."""


@dataclass
class RateLimit:
    limit: int = 0
    cost: int = 0
    remaining: int = 0
    reset_at: str = ""
    node_count: int = 0

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> RateLimit:
        payload = payload or {}
        return cls(
            limit=int(payload.get("limit") or 0),
            cost=int(payload.get("cost") or 0),
            remaining=int(payload.get("remaining") or 0),
            reset_at=str(payload.get("resetAt") or ""),
            node_count=int(payload.get("nodeCount") or 0),
        )


@dataclass
class Stats:
    pages: int = 0
    pull_requests: int = 0
    review_requested_events: int = 0
    reviews: int = 0
    force_pushes: int = 0
    reopened: int = 0
    bots_marked: int = 0
    points_spent: int = 0
    remaining: int = 0
    stopped_on_window: bool = False
    content_outside_window: int = 0
    page_size_reductions: int = 0
    commits_fetched: int = 0
    commits_truncated: int = 0


@dataclass
class ActionsStats:
    pages: int = 0
    requests: int = 0
    runs: int = 0
    total_count: int = 0
    mapped: int = 0
    reruns: int = 0
    failures: int = 0
    total_minutes: float = 0.0
    rerun_minutes: float = 0.0
    remaining: int = 0
    reported_total: int = 0
    unreachable: int = 0
    hit_pagination_cap: bool = False


# ---------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------


class RateLimiter:
    """Enforces both documented limits, plus a floor on request spacing.

    GitHub publishes three limits and we can only observe two of them. The
    primary 5,000/hour budget arrives on every response as `rateLimit`. The
    secondary 2,000-points-per-minute limit is not reported at all, so it is
    tracked client-side in a rolling window. The third — 90 seconds of server
    CPU per 60 seconds of wall clock — is unobservable from here; the minimum
    interval between requests is the only defence, and it is why this sleeps
    even when both budgets look healthy.
    """

    def __init__(
        self,
        points_per_minute: int = POINTS_PER_MINUTE,
        min_interval_s: float = MIN_REQUEST_INTERVAL_S,
        primary_floor: int = PRIMARY_FLOOR,
        sleep=time.sleep,
        now=time.monotonic,
    ) -> None:
        self.points_per_minute = points_per_minute
        self.min_interval_s = min_interval_s
        self.primary_floor = primary_floor
        self._sleep = sleep
        self._now = now
        self._window: deque[tuple[float, int]] = deque()
        self._last_request_at: float | None = None

    def _prune(self) -> None:
        cutoff = self._now() - 60.0
        while self._window and self._window[0][0] < cutoff:
            self._window.popleft()

    @property
    def points_in_window(self) -> int:
        self._prune()
        return sum(points for _, points in self._window)

    def before_request(self) -> None:
        if self._last_request_at is not None:
            elapsed = self._now() - self._last_request_at
            if elapsed < self.min_interval_s:
                self._sleep(self.min_interval_s - elapsed)
        if self.points_in_window >= self.points_per_minute:
            oldest = self._window[0][0]
            wait = max(0.0, 60.0 - (self._now() - oldest))
            logger.warning(
                "secondary limit: %d points in the last minute, sleeping %.1fs",
                self.points_in_window,
                wait,
            )
            self._sleep(wait)
        self._last_request_at = self._now()

    def after_response(self, rate_limit: RateLimit) -> None:
        self._window.append((self._now(), max(rate_limit.cost, 1)))
        logger.info(
            "rateLimit: cost=%d remaining=%d/%d resets=%s (%d pts/min used)",
            rate_limit.cost,
            rate_limit.remaining,
            rate_limit.limit,
            rate_limit.reset_at,
            self.points_in_window,
        )
        if rate_limit.remaining and rate_limit.remaining <= self.primary_floor:
            wait = _seconds_until(rate_limit.reset_at)
            logger.warning(
                "primary budget nearly exhausted (%d left), sleeping %.0fs until %s",
                rate_limit.remaining,
                wait,
                rate_limit.reset_at,
            )
            self._sleep(wait)

    def after_rest_response(self, headers) -> None:
        """REST reports its budget in headers, not in the response body.

        It is a different pool from GraphQL — 5,000 REQUESTS per hour rather
        than 5,000 points — so it is tracked separately and never mixed into
        the point window.
        """
        remaining = headers.get("x-ratelimit-remaining")
        reset = headers.get("x-ratelimit-reset")
        if remaining is None:
            return
        remaining = int(remaining)
        logger.info("REST rateLimit: remaining=%s", remaining)
        if remaining <= self.primary_floor and reset:
            wait = max(0.0, float(reset) - time.time()) + 1.0
            logger.warning(
                "REST budget nearly exhausted (%d left), sleeping %.0fs",
                remaining,
                wait,
            )
            self._sleep(wait)

    def backoff(self, attempt: int, retry_after: float | None = None) -> float:
        """Exponential backoff with full jitter, or Retry-After when given.

        Full jitter rather than a fixed multiplier: several of us may be
        hitting the API from one office network, and synchronised retries are
        how a secondary limit becomes a ban.
        """
        if retry_after is not None:
            delay = retry_after
        else:
            delay = random.uniform(0, min(60.0, 2.0**attempt))
        self._sleep(delay)
        return delay


def _seconds_until(iso_timestamp: str) -> float:
    if not iso_timestamp:
        return 60.0
    try:
        reset = datetime.fromisoformat(iso_timestamp)
    except ValueError:
        return 60.0
    return max(0.0, (reset - datetime.now(UTC)).total_seconds()) + 1.0


# ---------------------------------------------------------------------
# Scrubbing
# ---------------------------------------------------------------------


def redact_text(text: str | None, known_logins: Iterable[str] = ()) -> str | None:
    """Remove identity from free text: emails, @mentions, and bare logins.

    The bare-login pass is not optional and is easy to miss. GitHub names a
    fork's branch after its owner — `headRefName` comes back as
    `octocat/kafka-1` or `octocat-patch-1` — so a login reaches Postgres
    through a field that looks like a branch, with no `@` and no `login` key to
    catch it. `known_logins` is every login appearing anywhere in the same PR
    node: its author, its reviewers, and every timeline actor.

    `KAFKA-16234` and the surrounding prose survive, so the ticket-key fallback
    chain still works on the title, the branch and the body.
    """
    if not text:
        return text
    text = EMAIL_RE.sub("<email-redacted>", text)
    for login in sorted(known_logins, key=len, reverse=True):
        if not login:
            continue
        pattern = re.compile(
            rf"(?<![A-Za-z0-9]){re.escape(login)}(?![A-Za-z0-9])", re.IGNORECASE
        )
        text = pattern.sub(_short_hash(login), text)
    return MENTION_RE.sub(lambda m: f"@{_short_hash(m.group(1))}", text)


def _short_hash(login: str) -> str:
    return actor_hash(login)[:8]


def collect_logins(obj: Any) -> set[str]:
    """Every login anywhere in a node, so free text can be scrubbed of all of them."""
    found: set[str] = set()
    if isinstance(obj, dict):
        login = obj.get("login")
        if isinstance(login, str) and login:
            found.add(login)
        for value in obj.values():
            found |= collect_logins(value)
    elif isinstance(obj, list):
        for item in obj:
            found |= collect_logins(item)
    return found


def scrub_actor(node: Any) -> Any:
    """Replace {login, __typename} with {actor_hash, __typename, is_bot}.

    Bots are marked but never hashed: they are not people, they must not become
    actors, and hashing them would put a bot in the identity store where a
    later count of "how many humans" would include it.
    """
    if not isinstance(node, dict):
        return node
    login = node.get("login")
    if login is None:
        return node
    typename = node.get("__typename")
    bot = is_bot(login, typename)
    scrubbed = {"__typename": typename, "is_bot": bot}
    if not bot:
        scrubbed["actor_hash"] = actor_hash(login)
    return scrubbed


#: Free-text fields that may embed a login. `headRefName` is the dangerous one:
#: it carries identity with no `@` and no `login` key to give it away.
TEXT_FIELDS = frozenset({"title", "body", "headRefName"})


def flatten_labels(node: Any) -> list[str]:
    """`{nodes: [{name: "core"}]}` -> `["core"]`.

    A GitHub label is `{"name": "core"}` and a person is `{"name": "Ada
    Lovelace"}`. The guard cannot tell them apart from the key alone, and a
    guard that has to guess is not a guard — so rather than teaching it to
    distinguish two identical shapes by context, the shape is removed: a label
    set becomes a list of plain strings and no `name` key reaches Postgres.

    Same move as `workflow_name` in the Actions projection, and for the same
    reason. `name` is exactly what a person's name arrives under, so the day
    that key is allowed through anywhere is the day it gets through everywhere.
    """
    if isinstance(node, dict):
        node = node.get("nodes") or []
    if not isinstance(node, list):
        return []
    return [
        label["name"]
        for label in node
        if isinstance(label, dict) and isinstance(label.get("name"), str)
    ]


def scrub_payload(obj: Any, known_logins: Iterable[str] | None = None) -> Any:
    """Walk the response, hash every login, and redact identity from free text.

    Logins are collected from the whole node first, then redacted from its text
    fields — the author of a PR is rarely the only person named in it.
    """
    if known_logins is None:
        known_logins = collect_logins(obj)
    if isinstance(obj, dict):
        if "login" in obj:
            return scrub_actor(obj)
        out: dict[str, Any] = {}
        for key, value in obj.items():
            if key == "labels":
                out[key] = flatten_labels(value)
            elif key in TEXT_FIELDS:
                out[key] = redact_text(value, known_logins)
            else:
                out[key] = scrub_payload(value, known_logins)
        return out
    if isinstance(obj, list):
        return [scrub_payload(item, known_logins) for item in obj]
    return obj


def _assert_scrubbed(obj: Any, path: str = "$") -> None:
    """Fail loudly if a login survived. The last gate before Postgres."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key.lower() in {"login", "email", "name"}:
                raise AssertionError(
                    f"identity key {key!r} survived scrubbing at {path}.{key} — "
                    "this would write a GitHub login into Postgres"
                )
            _assert_scrubbed(value, f"{path}.{key}")
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            _assert_scrubbed(item, f"{path}[{i}]")


# ---------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------


class GitHubClient:
    """Thin GraphQL client with retry, backoff and budget awareness."""

    def __init__(
        self,
        token: str,
        client: httpx.Client | None = None,
        limiter: RateLimiter | None = None,
    ) -> None:
        if not token or not token.strip():
            raise MissingToken(
                "GITHUB_TOKEN is empty. GitHub's GraphQL API has no anonymous "
                "tier — unlike REST, every request must be authenticated. "
                "Create a classic PAT with no scopes (public repos need none) "
                "and put it in .env as GITHUB_TOKEN=ghp_..."
            )
        self.limiter = limiter or RateLimiter()
        self._client = client or httpx.Client(timeout=httpx.Timeout(60.0))
        # Set on the client we were given as well as the one we build, so an
        # injected client (tests, a proxy) is still authenticated.
        self._client.headers.update(
            {
                "Authorization": f"bearer {token.strip()}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "engineering-spend-intelligence/0.1",
            }
        )

    def execute(
        self,
        query: str,
        variables: dict[str, Any],
        max_retries: int = MAX_RETRIES,
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(max_retries):
            self.limiter.before_request()
            try:
                response = self._client.post(
                    API_URL, json={"query": query, "variables": variables}
                )
            except httpx.RequestError as exc:
                last_error = exc
                logger.warning("network error (%s), retrying", exc)
                self.limiter.backoff(attempt)
                continue

            if response.status_code in (403, 429):
                retry_after = response.headers.get("retry-after")
                delay = self.limiter.backoff(
                    attempt, float(retry_after) if retry_after else None
                )
                logger.warning(
                    "HTTP %d (secondary rate limit), backed off %.1fs",
                    response.status_code,
                    delay,
                )
                last_error = GitHubError(f"HTTP {response.status_code}")
                continue

            if response.status_code >= 500:
                last_error = GitHubError(f"HTTP {response.status_code}")
                self.limiter.backoff(attempt)
                continue

            if response.status_code != 200:
                raise GitHubError(f"HTTP {response.status_code}: {response.text[:300]}")

            try:
                payload = response.json()
            except ValueError as exc:
                # A 200 whose body stopped mid-string. GitHub cut the
                # connection partway through a 219 KB page and httpx handed
                # back what had arrived; only the JSON parser noticed. Every
                # status-code check above passed, so without this the run dies
                # 40 minutes in with a JSONDecodeError and no retry — the same
                # shape of bug as the ASF connection resets, where the
                # documented failure mode was not the real one.
                last_error = GitHubError(f"truncated response body: {exc}")
                logger.warning("truncated response body, retrying")
                self.limiter.backoff(attempt)
                continue

            if payload.get("errors"):
                types = {
                    e.get("type") for e in payload["errors"] if isinstance(e, dict)
                }
                if "RATE_LIMITED" in types:
                    self.limiter.backoff(attempt)
                    last_error = GitHubError("RATE_LIMITED")
                    continue
                raise GitHubError(f"GraphQL errors: {payload['errors']}")

            self.limiter.after_response(
                RateLimit.from_payload(payload.get("data", {}).get("rateLimit"))
            )
            return payload["data"]

        raise GitHubError(f"giving up after {max_retries} attempts: {last_error}")

    @property
    def http(self) -> httpx.Client:
        """The underlying client, shared by the GraphQL and REST paths so both
        go through one connection pool and one set of auth headers."""
        return self._client

    def close(self) -> None:
        self._client.close()


# ---------------------------------------------------------------------
# Cursor
# ---------------------------------------------------------------------


def load_cursor(session: Session, repo: str) -> str | None:
    return session.execute(
        select(IngestCursor.cursor).where(
            IngestCursor.source == SOURCE, IngestCursor.scope == repo
        )
    ).scalar_one_or_none()


def save_cursor(session: Session, repo: str, cursor: str | None) -> None:
    """Persist the page cursor so an interrupted run resumes.

    DEVIATION: the plan said to keep this in `raw_payload`. `ingest_cursor`
    exists in the frozen schema for exactly this, keyed (source, scope) where
    scope is "repo or jira project" — using raw_payload would mean inventing a
    fake entity_type and leaving a real table unused.

    Cleared to NULL on completion. A cursor is a resume token for an
    interrupted run, not a permanent watermark: because pages are ordered by
    UPDATED_AT DESC, resuming from a stale cursor after new activity would skip
    whatever moved to the front. A completed run therefore starts from the top
    next time and re-lands recent PRs, which the upsert makes free.
    """
    stmt = insert(IngestCursor).values(
        source=SOURCE, scope=repo, cursor=cursor, updated_at=datetime.now(UTC)
    )
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["source", "scope"],
            set_={
                "cursor": stmt.excluded.cursor,
                "updated_at": stmt.excluded.updated_at,
            },
        )
    )


# ---------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------


def _content_predates_window(node: dict[str, Any], cutoff: datetime) -> bool:
    """True if a PR is only in the feed because someone touched it recently.

    We page by UPDATED_AT DESC, which is the right filter for "was this active
    recently". But a PR created and merged in 2023 re-enters the feed the
    moment anyone comments on it, carrying 2023 timestamps.

    git_local learned this the hard way: 39 rebased Flink commits with old
    author dates stretched the global sprint grid from 26 windows to 63. The
    mapper decides which of these timestamps become events, so this module
    does not drop the row — it counts it and says so, loudly, in the report.
    """
    created = node.get("createdAt")
    merged = node.get("mergedAt")
    stamp = merged or created
    if not stamp:
        return False
    return datetime.fromisoformat(stamp) < cutoff


def _count_timeline(node: dict[str, Any], stats: Stats) -> None:
    for item in (node.get("timelineItems") or {}).get("nodes") or []:
        typename = (item or {}).get("__typename")
        if typename == "ReviewRequestedEvent":
            stats.review_requested_events += 1
        elif typename == "ReopenedEvent":
            stats.reopened += 1
        elif typename == "HeadRefForcePushedEvent":
            stats.force_pushes += 1
    stats.reviews += len((node.get("reviews") or {}).get("nodes") or [])

    commits = node.get("commits") or {}
    commit_nodes = commits.get("nodes") or []
    stats.commits_fetched += len(commit_nodes)
    if int(commits.get("totalCount") or 0) > len(commit_nodes):
        stats.commits_truncated += 1


def _write_page(session: Session, repo: str, nodes: list[dict[str, Any]]) -> int:
    rows = []
    for node in nodes:
        if not node:
            continue
        body = scrub_payload(node)
        body["repo"] = repo
        _assert_scrubbed(body)
        rows.append(
            {
                "source": SOURCE,
                "entity_type": ENTITY_TYPE,
                "entity_id": f"{repo}#{node['number']}",
                "body": body,
            }
        )
    if not rows:
        return 0
    assert_no_identity(rows)
    stmt = insert(RawPayload).values(rows)
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["source", "entity_type", "entity_id"],
            set_={"body": stmt.excluded.body, "fetched_at": datetime.now(UTC)},
        )
    )
    return len(rows)


def fetch_repo(
    repo: str,
    client: GitHubClient,
    session: Session,
    page_size: int = PR_PAGE_SIZE,
    max_pages: int | None = None,
    resume: bool = True,
) -> Stats:
    if not 1 <= page_size <= MAX_PAGE_SIZE:
        raise ValueError(f"page_size must be 1..{MAX_PAGE_SIZE}, got {page_size}")

    settings = get_settings()
    cutoff = datetime.now(UTC) - timedelta(days=round(settings.history_months * 30.44))
    owner, _, name = repo.partition("/")
    if not owner or not name:
        raise ValueError(f"--repo must be owner/name, got {repo!r}")

    cursor = load_cursor(session, repo) if resume else None
    if cursor:
        logger.info("resuming %s from stored cursor", repo)

    stats = Stats()
    while True:
        try:
            data = client.execute(
                PR_QUERY,
                {
                    "owner": owner,
                    "name": name,
                    "cursor": cursor,
                    "pageSize": page_size,
                },
                max_retries=RETRIES_BEFORE_SHRINK,
            )
        except GitHubError as exc:
            # THE PAGE IS TOO BIG, NOT THE NETWORK TOO FLAKY.
            #
            # Adding milestone, labels and mergedBy pushed the per-page cost
            # from 3 points to 4 and the response over whatever GitHub is
            # willing to stream: it answers 200, starts a chunked body, and
            # cuts it partway through. Retrying is useless — all six attempts
            # failed on the same cursor, every time.
            #
            # 100 PRs x (100 files + 20 reviews + 30 timeline items + 20
            # labels) is simply a lot of JSON. Halving the page and re-asking
            # from the SAME cursor costs one wasted request and gets a body
            # that arrives whole. Same lesson as ASF refusing maxResults=100.
            if not _page_too_expensive(exc) or page_size <= MIN_PAGE_SIZE:
                raise
            page_size = max(MIN_PAGE_SIZE, page_size // 2)
            stats.page_size_reductions += 1
            logger.warning(
                "GitHub could not deliver this page; halving page size to "
                "%d and retrying the same cursor",
                page_size,
            )
            continue
        rate = RateLimit.from_payload(data.get("rateLimit"))
        stats.points_spent += rate.cost
        stats.remaining = rate.remaining

        connection = ((data.get("repository") or {}).get("pullRequests")) or {}
        nodes = [n for n in (connection.get("nodes") or []) if n]
        stats.pages += 1

        in_window = []
        for node in nodes:
            updated = node.get("updatedAt")
            if updated and datetime.fromisoformat(updated) < cutoff:
                stats.stopped_on_window = True
                continue
            in_window.append(node)
            if _content_predates_window(node, cutoff):
                stats.content_outside_window += 1
            _count_timeline(node, stats)

        stats.pull_requests += _write_page(session, repo, in_window)

        page_info = connection.get("pageInfo") or {}
        cursor = page_info.get("endCursor")
        save_cursor(session, repo, cursor)
        session.commit()

        if stats.stopped_on_window:
            logger.info("reached the %d-month boundary", settings.history_months)
            break
        if not page_info.get("hasNextPage"):
            break
        if max_pages is not None and stats.pages >= max_pages:
            logger.info("stopping at --max-pages=%d", max_pages)
            return stats

    # Completed the window: clear the resume token, see save_cursor's docstring.
    save_cursor(session, repo, None)
    session.commit()
    return stats


def _print_report(repo: str, stats: Stats) -> None:
    print(f"\n=== {repo} ===")
    print(f"  pages fetched              {stats.pages}")
    print(f"  pull requests landed       {stats.pull_requests}")
    print("\n  timeline signal (this is why we use GraphQL, not REST)")
    print(f"    review_requested events  {stats.review_requested_events}")
    print(f"    reviews                  {stats.reviews}")
    print(f"    force pushes             {stats.force_pushes}")
    print(f"    reopened                 {stats.reopened}")
    if stats.review_requested_events == 0 and stats.pull_requests:
        print("    WARNING: zero review_requested events — review latency,")
        print("    one of our headline waste numbers, cannot be computed.")
    print(f"    commits fetched          {stats.commits_fetched}")
    if stats.commits_truncated:
        print(
            f"    WARNING: {stats.commits_truncated} PR(s) have >100 commits — "
            "truncated at the connection bound, undercounts those PRs only."
        )
    print("\n  budget")
    print(f"    points spent             {stats.points_spent}")
    print(f"    remaining this hour      {stats.remaining}")
    est = stats.points_spent / stats.pull_requests if stats.pull_requests else 0
    print(f"    points per PR            {est:.3f}  (REST equivalent: 4-5)\n")


def main(argv: Sequence[str] | None = None) -> int:
    warn_if_default_salt()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    settings = get_settings()

    parser = argparse.ArgumentParser(description="Fetch PRs into raw_payload.")
    parser.add_argument("--repo", action="append", help="owner/name; repeatable")
    parser.add_argument("--page-size", type=int, default=PR_PAGE_SIZE)
    parser.add_argument("--max-pages", type=int, default=None)
    parser.add_argument(
        "--reset-cursor",
        action="store_true",
        help="ignore any stored cursor and start from the newest PR",
    )
    parser.add_argument("--prs", action="store_true", help="fetch pull requests")
    parser.add_argument("--actions", action="store_true", help="fetch Actions runs")
    args = parser.parse_args(argv)
    repos = args.repo or settings.github_repo_list
    # Neither flag means both, so the bare command still does the whole job.
    do_prs = args.prs or not args.actions
    do_actions = args.actions or not args.prs

    try:
        client = GitHubClient(settings.github_token)
    except MissingToken as exc:
        print(f"\nERROR: {exc}\n", file=sys.stderr)
        return 2

    pr_results: list[tuple[str, Stats]] = []
    action_results: list[tuple[str, ActionsStats]] = []
    try:
        with write_session() as session:
            # Sequential, never concurrent: the GraphQL and REST budgets are
            # separate pools but GitHub's concurrency and abuse limits are not.
            for repo in repos:
                if do_prs:
                    pr_results.append(
                        (
                            repo,
                            fetch_repo(
                                repo,
                                client,
                                session,
                                page_size=args.page_size,
                                max_pages=args.max_pages,
                                resume=not args.reset_cursor,
                            ),
                        )
                    )
                if do_actions:
                    action_results.append(
                        (
                            repo,
                            fetch_actions_runs(
                                repo,
                                client,
                                session,
                                page_size=args.page_size,
                                max_pages=args.max_pages,
                            ),
                        )
                    )
    finally:
        client.close()

    for repo, stats in pr_results:
        _print_report(repo, stats)
    for repo, astats in action_results:
        _print_actions_report(repo, astats)
    return 0


# ---------------------------------------------------------------------
# Actions runs (REST)
# ---------------------------------------------------------------------


def _rest_get(
    client: GitHubClient, path: str, params: dict[str, Any]
) -> tuple[dict[str, Any], httpx.Headers]:
    """One REST GET with the same retry, backoff and spacing as GraphQL."""
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        client.limiter.before_request()
        try:
            response = client.http.get(f"{REST_BASE}{path}", params=params)
        except httpx.RequestError as exc:
            last_error = exc
            client.limiter.backoff(attempt)
            continue

        if response.status_code in (403, 429):
            retry_after = response.headers.get("retry-after")
            client.limiter.backoff(attempt, float(retry_after) if retry_after else None)
            last_error = GitHubError(f"HTTP {response.status_code}")
            continue
        if response.status_code >= 500:
            last_error = GitHubError(f"HTTP {response.status_code}")
            client.limiter.backoff(attempt)
            continue
        if response.status_code != 200:
            raise GitHubError(f"HTTP {response.status_code}: {response.text[:300]}")

        try:
            payload = response.json()
        except ValueError as exc:  # truncated body; see execute()
            last_error = GitHubError(f"truncated response body: {exc}")
            logger.warning("truncated REST body, retrying")
            client.limiter.backoff(attempt)
            continue

        client.limiter.after_rest_response(response.headers)
        return payload, response.headers

    raise GitHubError(f"giving up after {MAX_RETRIES} attempts: {last_error}")


def keep_run_fields(run: dict[str, Any]) -> dict[str, Any]:
    """Project a workflow run down to the fields we actually use.

    A run response is large and carries the full repository object, the
    triggering actor, and `head_commit` with an author NAME and EMAIL. None of
    that is needed, and one field of it is identity, so this projection is a
    privacy decision as much as a size one.
    """
    kept = {key: run.get(key) for key in RUN_FIELDS}
    # Stored as `workflow_name`, not `name`. A workflow name ("CI",
    # "build-and-test") is not identity, but `_assert_scrubbed` rejects any key
    # called `name` and it is right to — a bare `name` key is exactly what a
    # person's name arrives under. Renaming the safe one keeps the guard strict
    # instead of teaching it an exception it would later apply too broadly.
    kept["workflow_name"] = kept.pop("name", None)
    return kept


def wall_clock_minutes(run: dict[str, Any]) -> float:
    """Duration proxy. See CI_DURATION_NOTE for what this is and is not."""
    started, updated = run.get("run_started_at"), run.get("updated_at")
    if not started or not updated:
        return 0.0
    delta = datetime.fromisoformat(updated) - datetime.fromisoformat(started)
    return max(0.0, delta.total_seconds() / 60.0)


def record_ci_duration_basis(session: Session) -> None:
    """Put the disclosure in the database, not in a slide footnote."""
    stmt = insert(RunConfig).values(
        key="ci_duration_basis", value=CI_DURATION_BASIS, note=CI_DURATION_NOTE
    )
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["key"],
            set_={"value": stmt.excluded.value, "note": stmt.excluded.note},
        )
    )


def sha_to_work_item(session: Session) -> dict[str, str]:
    """Map every known commit sha to its case.

    Built from `event_log.attrs->>'sha'` rather than `work_item.source_ref`:
    source_ref holds one sha per case, but a case has many commits and a CI run
    can be triggered by any one of them.
    """
    rows = session.execute(
        select(EventLog.attrs["sha"].astext, EventLog.work_item_id).where(
            EventLog.activity == "commit"
        )
    ).all()
    return {sha: work_item_id for sha, work_item_id in rows if sha}


def _write_runs(
    session: Session,
    repo: str,
    runs: list[dict[str, Any]],
    sha_map: dict[str, str],
    stats: ActionsStats,
) -> int:
    raw_rows: list[dict[str, Any]] = []
    ci_rows: list[dict[str, Any]] = []
    for run in runs:
        run_id = str(run.get("id"))
        head_sha = run.get("head_sha")
        work_item_id = sha_map.get(head_sha) if head_sha else None
        attempt = int(run.get("run_attempt") or 1)
        minutes = wall_clock_minutes(run)

        if work_item_id:
            stats.mapped += 1
        if attempt > 1:
            stats.reruns += 1
            stats.rerun_minutes += minutes
        if run.get("conclusion") == "failure":
            stats.failures += 1
        stats.total_minutes += minutes

        raw_rows.append(
            {
                "source": ACTIONS_SOURCE,
                "entity_type": ACTIONS_ENTITY_TYPE,
                "entity_id": f"{repo}#{run_id}",
                "body": {**run, "repo": repo},
            }
        )
        ci_rows.append(
            {
                "run_id": f"{repo}#{run_id}",
                "work_item_id": work_item_id,
                "repo": repo,
                "head_sha": head_sha,
                "ts": datetime.fromisoformat(run["run_started_at"])
                if run.get("run_started_at")
                else datetime.now(UTC),
                "runner_minutes": round(minutes, 3),
                "conclusion": run.get("conclusion"),
                "attempt": attempt,
            }
        )

    if not raw_rows:
        return 0

    assert_no_identity(raw_rows)
    for row in raw_rows:
        _assert_scrubbed(row["body"])
    raw_stmt = insert(RawPayload).values(raw_rows)
    session.execute(
        raw_stmt.on_conflict_do_update(
            index_elements=["source", "entity_type", "entity_id"],
            set_={"body": raw_stmt.excluded.body, "fetched_at": datetime.now(UTC)},
        )
    )

    assert_no_identity(ci_rows)
    ci_stmt = insert(CiRun).values(ci_rows)
    session.execute(
        ci_stmt.on_conflict_do_update(
            index_elements=["run_id"],
            set_={
                "work_item_id": ci_stmt.excluded.work_item_id,
                "runner_minutes": ci_stmt.excluded.runner_minutes,
                "conclusion": ci_stmt.excluded.conclusion,
                "attempt": ci_stmt.excluded.attempt,
            },
        )
    )
    return len(ci_rows)


def _fetch_window(
    client: GitHubClient,
    session: Session,
    repo: str,
    owner: str,
    name: str,
    since: date,
    until: date,
    sha_map: dict[str, str],
    stats: ActionsStats,
    page_size: int,
) -> None:
    """Fetch one date window, bisecting it if GitHub cannot paginate it all.

    THE 1,000-RESULT CAP IS THE WHOLE REASON THIS IS RECURSIVE.

    `GET /actions/runs` reports `total_count` honestly — 103,888 for apache/kafka
    over 12 months — but refuses to paginate past 1,000 results. A flat pager
    therefore collects the most recent 1% and silently calls it the year. Every
    per-sprint CI cost computed from that would be wrong, and wrong in a way
    that looks fine: the rows are real, there are just almost none of them for
    any sprint older than a fortnight.

    So each window asks for its own `total_count` first. Under the cap, page it
    normally. Over the cap, split the window in half and recurse — each half
    gets its own fresh 1,000 allowance. Windows are only split where the data
    is actually dense, so quiet weeks still cost one request.
    """
    payload, headers = _rest_get(
        client,
        f"/repos/{owner}/{name}/actions/runs",
        {
            "per_page": page_size,
            "page": 1,
            "created": f"{since.isoformat()}..{until.isoformat()}",
        },
    )
    stats.requests += 1
    stats.remaining = int(headers.get("x-ratelimit-remaining") or 0)
    total = int(payload.get("total_count") or 0)

    if total > REST_PAGINATION_CAP and since < until:
        midpoint = since + (until - since) / 2
        _fetch_window(
            client,
            session,
            repo,
            owner,
            name,
            since,
            midpoint,
            sha_map,
            stats,
            page_size,
        )
        _fetch_window(
            client,
            session,
            repo,
            owner,
            name,
            midpoint + timedelta(days=1),
            until,
            sha_map,
            stats,
            page_size,
        )
        return

    if total > REST_PAGINATION_CAP:
        # A single day with more than 1,000 runs cannot be split further.
        logger.warning(
            "%s %s has %d runs in one day; only %d are reachable",
            repo,
            since.isoformat(),
            total,
            REST_PAGINATION_CAP,
        )
        stats.unreachable += total - REST_PAGINATION_CAP
        stats.hit_pagination_cap = True

    # Count the window's total ONLY here, at a leaf. Counting it before the
    # bisection above added every parent window on top of its own children —
    # apache/kafka reported 878,404 runs against a real 103,888 and the
    # coverage line read 11.8% when the true figure was 100%.
    stats.reported_total += total

    runs = payload.get("workflow_runs") or []
    if not runs:
        return
    stats.runs += _write_runs(
        session, repo, [keep_run_fields(r) for r in runs], sha_map, stats
    )
    stats.pages += 1

    page = 2
    while len(runs) == page_size and (page - 1) * page_size < min(
        total, REST_PAGINATION_CAP
    ):
        payload, headers = _rest_get(
            client,
            f"/repos/{owner}/{name}/actions/runs",
            {
                "per_page": page_size,
                "page": page,
                "created": f"{since.isoformat()}..{until.isoformat()}",
            },
        )
        stats.requests += 1
        stats.remaining = int(headers.get("x-ratelimit-remaining") or 0)
        runs = payload.get("workflow_runs") or []
        if not runs:
            break
        stats.runs += _write_runs(
            session, repo, [keep_run_fields(r) for r in runs], sha_map, stats
        )
        stats.pages += 1
        page += 1
    session.commit()


def fetch_actions_runs(
    repo: str,
    client: GitHubClient,
    session: Session,
    page_size: int = MAX_PAGE_SIZE,
    max_pages: int | None = None,
) -> ActionsStats:
    if not 1 <= page_size <= MAX_PAGE_SIZE:
        raise ValueError(f"page_size must be 1..{MAX_PAGE_SIZE}, got {page_size}")
    owner, _, name = repo.partition("/")
    if not owner or not name:
        raise ValueError(f"--repo must be owner/name, got {repo!r}")

    settings = get_settings()
    cutoff = datetime.now(UTC) - timedelta(days=round(settings.history_months * 30.44))

    record_ci_duration_basis(session)
    sha_map = sha_to_work_item(session)
    logger.info("%d known commit shas available to join against", len(sha_map))

    stats = ActionsStats()
    _fetch_window(
        client,
        session,
        repo,
        owner,
        name,
        cutoff.date(),
        datetime.now(UTC).date(),
        sha_map,
        stats,
        page_size,
    )
    session.commit()
    return stats


def _print_actions_report(repo: str, stats: ActionsStats) -> None:
    print(f"\n=== {repo} — Actions runs ===")
    print(f"  requests                   {stats.requests}")
    print(f"  runs reported by GitHub    {stats.reported_total:,}")
    print(f"  ci_run rows written        {stats.runs:,}")
    coverage = stats.runs / stats.reported_total if stats.reported_total else 0
    print(f"  coverage of the window     {coverage:.1%}")
    mapped_pct = stats.mapped / stats.runs if stats.runs else 0
    print(f"  joined to a work item      {stats.mapped:,}  ({mapped_pct:.1%})")
    print("    unmapped runs keep a null work_item_id: head_sha is often a")
    print("    merge ref that never appears as a commit in our clone.")
    print("\n  CI waste signal")
    print(f"    reruns (attempt > 1)     {stats.reruns:,}")
    print(f"    failed runs              {stats.failures:,}")
    print(f"    total wall-clock minutes {stats.total_minutes:,.0f}")
    print(f"    minutes spent on reruns  {stats.rerun_minutes:,.0f}")
    share = stats.rerun_minutes / stats.total_minutes if stats.total_minutes else 0
    print(f"    RERUN SHARE OF CI TIME   {share:.1%}   <- the waste number")
    print(f"\n  duration basis             {CI_DURATION_BASIS} (in run_config)")
    if stats.hit_pagination_cap:
        print(
            f"  WARNING: {stats.unreachable:,} runs are unreachable — a single day "
            f"exceeded\n  GitHub's {REST_PAGINATION_CAP}-result pagination cap and "
            "cannot be split further.\n  CI totals for those days are a floor, not "
            "a total. Say so on the slide."
        )
    print(f"  REST requests remaining    {stats.remaining}\n")


if __name__ == "__main__":
    sys.exit(main())
