"""
GitHub GraphQL connector — FETCH ONLY.
Owner: Nishant (ingestion lane).
Phase: Tier 0.

    python -m app.ingestion.github_connector --repo apache/kafka

Writes to `raw_payload` and `ingest_cursor`. Nothing else. No `event_log`, no
`actor`, no `work_item` — Dipen owns the mapping in `normalise/`, and this
module deliberately has no import that would let it write those tables.

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
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import IngestCursor, RawPayload
from app.db.session import write_session
from app.ingestion.pseudonymize import actor_hash, assert_no_identity, is_bot

logger = logging.getLogger(__name__)

SOURCE = "github_graphql"
ENTITY_TYPE = "pull_request"
API_URL = "https://api.github.com/graphql"

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
        files(first: 100) { nodes { path } }
        reviews(first: 20) {
          nodes { state submittedAt author { login __typename } }
        }
        timelineItems(
          first: 30
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
            if key in TEXT_FIELDS:
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


class GitHubGraphQL:
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

    def execute(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES):
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

            payload = response.json()
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

        raise GitHubError(f"giving up after {MAX_RETRIES} attempts: {last_error}")

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
    client: GitHubGraphQL,
    session: Session,
    page_size: int = MAX_PAGE_SIZE,
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
        data = client.execute(
            PR_QUERY,
            {
                "owner": owner,
                "name": name,
                "cursor": cursor,
                "pageSize": page_size,
            },
        )
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
    print("\n  budget")
    print(f"    points spent             {stats.points_spent}")
    print(f"    remaining this hour      {stats.remaining}")
    est = stats.points_spent / stats.pull_requests if stats.pull_requests else 0
    print(f"    points per PR            {est:.3f}  (REST equivalent: 4-5)\n")


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    settings = get_settings()

    parser = argparse.ArgumentParser(description="Fetch PRs into raw_payload.")
    parser.add_argument("--repo", action="append", help="owner/name; repeatable")
    parser.add_argument("--page-size", type=int, default=MAX_PAGE_SIZE)
    parser.add_argument("--max-pages", type=int, default=None)
    parser.add_argument(
        "--reset-cursor",
        action="store_true",
        help="ignore any stored cursor and start from the newest PR",
    )
    args = parser.parse_args(argv)
    repos = args.repo or settings.github_repo_list

    try:
        client = GitHubGraphQL(settings.github_token)
    except MissingToken as exc:
        print(f"\nERROR: {exc}\n", file=sys.stderr)
        return 2

    try:
        with write_session() as session:
            results = [
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
                for repo in repos
            ]
    finally:
        client.close()

    for repo, stats in results:
        _print_report(repo, stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
