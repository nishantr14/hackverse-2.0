"""
ASF Jira connector — fetch and land, no interpretation.
Owner: Nishant (ingestion lane).
Phase: Tier 0.

    python -m app.ingestion.jira_connector --project KAFKA

Writes `raw_payload` and `ingest_cursor`. Nothing else. No `event_log`, no
`work_item`, no metrics — Dipen's `normalise/` decides which timestamps become
events and which fall outside the analysis window.

CHANGELOGS COME FROM SEARCH, NOT ONE REQUEST PER ISSUE
------------------------------------------------------
The plan called for `GET /rest/api/2/issue/{key}/changelog` per issue. Measured
against the live ASF instance, KAFKA has 2,155 issues updated in a 12-month
window, so that is 2,199 requests. `search` accepts `expand=changelog` and
returns the histories inline:

    per-issue changelog   2,199 requests  (~8 hours at a polite pace)
    search + expand        44 requests    (~10 minutes)

Same data, 98% fewer requests against a nonprofit's infrastructure. The
per-issue endpoint is still implemented and still used — but only for the
issues whose inline changelog comes back truncated, which was zero out of the
first 50 measured.

ASF DROPS CONNECTIONS
---------------------
The documented failure mode is 429/5xx. The ACTUAL failure mode, measured, is
the TCP connection being closed mid-request: three consecutive `ConnectError`s
before the first success, and a `ConnectTimeout` after that. A retry policy
that only covers HTTP status codes never sees these — the exception is raised
before there is a response to inspect. `RETRYABLE_ERRORS` covers both.

A page of 100 issues with inline changelogs was refused outright. 50 works and
takes ~11s. Requests are serial, spaced, and never parallelised.

OLDER STATUS TRANSITIONS ARE PRESERVED
--------------------------------------
An issue updated yesterday can carry a status transition from 2019. Those
transitions are landed exactly as returned — 3 of them appeared in the first 50
issues sampled. Dropping them here would silently destroy the backlog-time
metric, and this connector is not the place that decides what is in scope.
"""

from __future__ import annotations

import argparse
import logging
import random
import re
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import IngestCursor, RawPayload, WorkItem
from app.db.session import write_session
from app.ingestion.pseudonymize import actor_hash, assert_no_identity, is_bot

logger = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+[.][\w.-]+")

SOURCE = "asf_jira"
ISSUE_ENTITY = "issue"
CHANGELOG_ENTITY = "changelog"

#: 100 was refused by the live instance; 50 is served in ~11s.
DEFAULT_PAGE_SIZE = 50
#: Serial and spaced. This is a nonprofit's shared infrastructure.
REQUEST_INTERVAL_S = 2.0
MAX_RETRIES = 6

#: Connection resets are the real failure mode here, not status codes.
RETRYABLE_ERRORS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.RemoteProtocolError,
    httpx.ReadError,
)

#: The issue record supplies the case spine. `updated` is not in the plan's
#: field list but is required by it: without it there is no way to tell whether
#: a landed issue has changed, and "do not re-fetch unchanged data" is
#: unimplementable.
ISSUE_FIELDS = (
    "issuetype",
    "priority",
    "status",
    "resolution",
    "created",
    "resolutiondate",
    "components",
    "parent",
    "summary",
    "updated",
)

#: Objects that ARE a Jira user. Replaced wholesale, never filtered field by
#: field, so a key Atlassian adds later cannot quietly reintroduce identity.
USER_OBJECT_KEYS = frozenset(
    {"author", "reporter", "assignee", "creator", "updateAuthor"}
)

#: Changelog entries whose VALUES are people. This is the subtle one: an entry
#: for field "assignee" carries
#:     {"field": "assignee", "from": "jsmith", "fromString": "J Smith", ...}
#: — a username and a full display name, in a payload with no user object in it
#: at all. Scrubbing only user objects would sail straight past this.
USER_VALUED_FIELDS = frozenset(
    {"assignee", "reporter", "creator", "watcher", "request participants"}
)

#: URL and avatar keys embed the username in a path. Nothing downstream reads
#: them, so they are dropped rather than scrubbed.
DROP_KEYS = frozenset({"self", "avatarUrls"})

#: Keys that are identity WHEREVER they appear. Deliberately does NOT include
#: `name` or `key`: Jira uses those for status.name, priority.name,
#: components[].name and parent.key, which are domain vocabulary the mapper
#: needs. User objects are replaced wholesale, so the only `name` values that
#: survive are the names of things, not of people.
IDENTITY_KEYS = frozenset({"displayname", "emailaddress", "avatarurls", "timezone"})


class JiraError(RuntimeError):
    """Non-retryable failure from the ASF Jira API."""


@dataclass
class Stats:
    pages: int = 0
    requests: int = 0
    issues_seen: int = 0
    issues_landed: int = 0
    issues_skipped_unchanged: int = 0
    changelog_topups: int = 0
    status_transitions: int = 0
    transitions_before_window: int = 0
    truncated_changelogs: int = 0
    reported_total: int = 0
    retries: int = 0
    keys: set[str] = field(default_factory=set)


# ---------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------


def scrub_author(author: Any) -> dict[str, Any] | None:
    """Replace a Jira user object with an actor_hash.

    A changelog author arrives as::

        {"self": ..., "name": "jsmith", "key": "jsmith",
         "emailAddress": "jsmith@apache.org", "displayName": "J Smith", ...}

    A username, an email and a real name in one object.

    The ASF username is the hash input, matching how `identity_key_from_email`
    reduces `jsmith@apache.org` to `jsmith` in git_local — so one human is one
    actor across git, GitHub and Jira instead of three.
    """
    if not isinstance(author, dict):
        return None
    handle = author.get("name") or author.get("key") or ""
    email = author.get("emailAddress") or ""
    if not handle and email:
        handle = email.split("@", 1)[0]
    if not handle:
        return {"actor_hash": None, "is_bot": True}
    bot = is_bot(handle)
    out: dict[str, Any] = {"is_bot": bot}
    if not bot:
        out["actor_hash"] = actor_hash(handle)
    return out


def scrub_changelog_item(item: dict[str, Any]) -> dict[str, Any]:
    """Hash the from/to values of a changelog entry that records a person.

    A status transition keeps its `fromString`/`toString` ("Open" -> "Patch
    Available") because those are the state names the process graph is built
    from. An assignee transition does not: its from/to are people.
    """
    field_name = str(item.get("field") or "").strip().lower()
    if field_name not in USER_VALUED_FIELDS:
        return {k: scrub_jira(v) for k, v in item.items()}
    out = dict(item)
    for key in ("from", "to", "fromString", "toString"):
        value = out.get(key)
        out[key] = actor_hash(str(value)) if value else value
    return out


def scrub_jira(obj: Any) -> Any:
    """Walk a Jira payload replacing every person in it with an actor_hash."""
    if isinstance(obj, dict):
        if "field" in obj and "fieldtype" in obj:
            return scrub_changelog_item(obj)
        out: dict[str, Any] = {}
        for key, value in obj.items():
            if key in DROP_KEYS:
                continue
            if key in USER_OBJECT_KEYS:
                out[key] = scrub_author(value)
            else:
                out[key] = scrub_jira(value)
        return out
    if isinstance(obj, list):
        return [scrub_jira(item) for item in obj]
    return obj


def assert_no_jira_identity(obj: Any, path: str = "$") -> None:
    """Last gate before Postgres: identity keys, and email-shaped values.

    The value scan matters as much as the key scan. Jira puts real names in
    string positions — a `toString` on an assignee change is a person's name
    under a key that reads like plumbing.
    """
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key.lower() in IDENTITY_KEYS:
                raise AssertionError(
                    f"identity key {key!r} survived scrubbing at {path}.{key} — "
                    "this would write a Jira username or real name into Postgres"
                )
            assert_no_jira_identity(value, f"{path}.{key}")
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            assert_no_jira_identity(item, f"{path}[{i}]")
    elif isinstance(obj, str) and EMAIL_RE.search(obj):
        raise AssertionError(f"email-shaped value at {path}: {obj[:40]!r}")


# ---------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------


class JiraClient:
    """Anonymous, serial, spaced, and patient. No token; ASF read is open."""

    def __init__(
        self,
        base_url: str,
        client: httpx.Client | None = None,
        interval_s: float = REQUEST_INTERVAL_S,
        sleep=time.sleep,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.interval_s = interval_s
        self._sleep = sleep
        self._last_request_at: float | None = None
        self.retries = 0
        self.requests = 0
        self._client = client or httpx.Client(timeout=httpx.Timeout(120.0))
        # Applied to an injected client too, not just the one we build. ASF is
        # a nonprofit running shared infrastructure; identifying ourselves is
        # the deal for anonymous access, and a proxy or test client must not
        # silently opt out of it.
        self._client.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": (
                    "engineering-spend-intelligence/0.1 "
                    "(HackVerse 2.0 student project; anonymous read only)"
                ),
            }
        )

    def get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        if self._last_request_at is not None:
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < self.interval_s:
                self._sleep(self.interval_s - elapsed)

        last: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                self.requests += 1
                response = self._client.get(f"{self.base_url}{path}", params=params)
            except RETRYABLE_ERRORS as exc:
                last = exc
                self.retries += 1
                self._backoff(attempt)
                logger.warning("%s on %s, retrying", type(exc).__name__, path)
                continue
            finally:
                self._last_request_at = time.monotonic()

            if response.status_code == 429 or response.status_code >= 500:
                retry_after = response.headers.get("retry-after")
                self.retries += 1
                self._backoff(attempt, float(retry_after) if retry_after else None)
                last = JiraError(f"HTTP {response.status_code}")
                continue
            if response.status_code == 404:
                raise JiraError(f"404 for {path} — check the project key")
            if response.status_code != 200:
                raise JiraError(f"HTTP {response.status_code}: {response.text[:300]}")
            return response.json()

        raise JiraError(f"giving up after {MAX_RETRIES} attempts: {last}")

    def _backoff(self, attempt: int, retry_after: float | None = None) -> None:
        delay = (
            retry_after
            if retry_after is not None
            else random.uniform(1.0, min(60.0, 2.0 ** (attempt + 1)))
        )
        self._sleep(delay)

    def close(self) -> None:
        self._client.close()


# ---------------------------------------------------------------------
# Landing
# ---------------------------------------------------------------------


def landed_updated_at(session: Session, project: str) -> dict[str, str]:
    """Every already-landed issue key and the `updated` we stored for it.

    This is what makes "do not re-fetch what has not changed" real rather than
    aspirational.
    """
    rows = session.execute(
        select(RawPayload.entity_id, RawPayload.body["fields"]["updated"].astext).where(
            RawPayload.source == SOURCE,
            RawPayload.entity_type == ISSUE_ENTITY,
            RawPayload.entity_id.like(f"{project}-%"),
        )
    ).all()
    return {key: updated for key, updated in rows if updated}


def _write_issue(session: Session, issue: dict[str, Any]) -> None:
    """Land the issue spine and its changelog as two rows keyed by issue key.

    The Jira key IS the entity_id — `KAFKA-12345`, unchanged. git_local already
    resolves commit subjects to that exact string and uses it as
    `work_item_id`, so the two sources join on it with no translation table and
    no second identifier for one issue.
    """
    key = issue["key"]
    changelog = issue.get("changelog") or {}

    spine = scrub_jira(
        {
            "key": key,
            "id": issue.get("id"),
            "fields": issue.get("fields") or {},
        }
    )
    histories = scrub_jira(changelog.get("histories") or [])
    changelog_body = {
        "key": key,
        "total": changelog.get("total"),
        "returned": len(changelog.get("histories") or []),
        "histories": histories,
    }

    for body in (spine, changelog_body):
        assert_no_jira_identity(body, path=f"${key}")

    rows = [
        {
            "source": SOURCE,
            "entity_type": ISSUE_ENTITY,
            "entity_id": key,
            "body": spine,
        },
        {
            "source": SOURCE,
            "entity_type": CHANGELOG_ENTITY,
            "entity_id": key,
            "body": changelog_body,
        },
    ]
    assert_no_identity(rows)
    stmt = insert(RawPayload).values(rows)
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["source", "entity_type", "entity_id"],
            set_={"body": stmt.excluded.body, "fetched_at": datetime.now(UTC)},
        )
    )


def _count_transitions(issue: dict[str, Any], cutoff: datetime, stats: Stats) -> None:
    changelog = issue.get("changelog") or {}
    for history in changelog.get("histories") or []:
        for item in history.get("items") or []:
            if item.get("field") != "status":
                continue
            stats.status_transitions += 1
            created = history.get("created")
            if created and _parse_jira_ts(created) < cutoff:
                # Preserved deliberately. See the module docstring.
                stats.transitions_before_window += 1


def _parse_jira_ts(value: str) -> datetime:
    """Jira Server returns 2026-03-01T10:00:00.000+0000 — no colon in offset."""
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%f%z")


def save_watermark(session: Session, project: str, updated: str | None) -> None:
    stmt = insert(IngestCursor).values(
        source=SOURCE, scope=project, cursor=updated, updated_at=datetime.now(UTC)
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


def fetch_project(
    project: str,
    client: JiraClient,
    session: Session,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_pages: int | None = None,
) -> Stats:
    settings = get_settings()
    cutoff = datetime.now(UTC) - timedelta(days=round(settings.history_months * 30.44))
    since = cutoff.date().isoformat()

    stats = Stats()
    known = landed_updated_at(session, project)
    logger.info("%d %s issues already landed", len(known), project)

    jql = f'project = {project} AND updated >= "{since}" ORDER BY updated ASC'
    start_at = 0
    while True:
        payload = client.get(
            "/rest/api/2/search",
            {
                "jql": jql,
                "startAt": start_at,
                "maxResults": page_size,
                "fields": ",".join(ISSUE_FIELDS),
                "expand": "changelog",
            },
        )
        stats.pages += 1
        stats.reported_total = int(payload.get("total") or 0)
        issues = payload.get("issues") or []
        if not issues:
            break

        for issue in issues:
            key = issue["key"]
            stats.issues_seen += 1
            stats.keys.add(key)
            updated = ((issue.get("fields") or {}).get("updated")) or ""

            if known.get(key) == updated and updated:
                stats.issues_skipped_unchanged += 1
                continue

            changelog = issue.get("changelog") or {}
            total = int(changelog.get("total") or 0)
            if total > len(changelog.get("histories") or []):
                # Inline changelog truncated: this is the only case that needs
                # the per-issue endpoint the plan specified.
                stats.truncated_changelogs += 1
                issue["changelog"] = _fetch_full_changelog(client, key, stats)

            _count_transitions(issue, cutoff, stats)
            _write_issue(session, issue)
            stats.issues_landed += 1

        session.commit()
        start_at += len(issues)
        if start_at >= stats.reported_total:
            break
        if max_pages is not None and stats.pages >= max_pages:
            logger.info("stopping at --max-pages=%d", max_pages)
            break

    stats.requests = client.requests
    stats.retries = client.retries
    save_watermark(session, project, datetime.now(UTC).isoformat())
    session.commit()
    return stats


def _fetch_full_changelog(client: JiraClient, key: str, stats: Stats) -> dict[str, Any]:
    """Page the per-issue changelog endpoint. Used only when search truncates."""
    histories: list[dict[str, Any]] = []
    start_at, total = 0, None
    while True:
        payload = client.get(
            f"/rest/api/2/issue/{key}/changelog",
            {"startAt": start_at, "maxResults": 100},
        )
        stats.changelog_topups += 1
        values = payload.get("values") or payload.get("histories") or []
        histories.extend(values)
        total = int(payload.get("total") or len(histories))
        start_at += len(values)
        if not values or start_at >= total:
            break
    return {"total": total, "histories": histories}


# ---------------------------------------------------------------------
# Verification report
# ---------------------------------------------------------------------


def _print_report(project: str, stats: Stats, session: Session) -> None:
    keys = sorted(stats.keys)
    overlap = 0
    if keys:
        overlap = session.execute(
            select(func.count())
            .select_from(WorkItem)
            .where(WorkItem.work_item_id.in_(keys))
        ).scalar_one()
    pct = overlap / len(keys) if keys else 0.0

    dupes = session.execute(
        select(func.count()).select_from(
            select(RawPayload.entity_id)
            .where(RawPayload.source == SOURCE, RawPayload.entity_type == ISSUE_ENTITY)
            .group_by(RawPayload.entity_id)
            .having(func.count() > 1)
            .subquery()
        )
    ).scalar_one()

    malformed = [k for k in keys if not k.startswith(f"{project}-")]

    print(f"\n=== {project} ===")
    print(f"  issues reported by Jira    {stats.reported_total:,}")
    print(f"  pages                      {stats.pages}")
    print(f"  HTTP requests              {stats.requests}   (retries: {stats.retries})")
    print(
        f"  per-issue changelog calls  {stats.changelog_topups}  "
        f"(only for {stats.truncated_changelogs} truncated inline changelogs)"
    )
    print("\n  verification")
    print(f"  1. distinct issue keys fetched          {len(keys):,}")
    print(f"  2. already in work_item from git        {overlap:,}")
    print(f"  3. OVERLAP                              {pct:.1%}")
    print(f"  4. status-transition changelog events   {stats.status_transitions:,}")
    print(
        f"       of which predate the window        {stats.transitions_before_window:,}"
        "  (preserved, not dropped)"
    )
    print(
        f"  5. duplicate issue payloads             {dupes}  "
        f"{'OK' if dupes == 0 else 'FAIL'}"
    )
    print(
        f"  6. keys not matching {project}-N format {len(malformed)}  "
        f"{'OK' if not malformed else malformed[:5]}"
    )
    print(
        f"\n  landed {stats.issues_landed:,}, skipped unchanged "
        f"{stats.issues_skipped_unchanged:,}\n"
    )


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    settings = get_settings()

    parser = argparse.ArgumentParser(description="Land ASF Jira issues + changelogs.")
    parser.add_argument("--project", action="append", help="e.g. KAFKA; repeatable")
    parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE)
    parser.add_argument("--max-pages", type=int, default=None)
    args = parser.parse_args(argv)
    projects = args.project or settings.jira_project_list

    client = JiraClient(settings.asf_jira_base_url)
    try:
        with write_session() as session:
            results = [
                (p, fetch_project(p, client, session, args.page_size, args.max_pages))
                for p in projects
            ]
            for project, stats in results:
                _print_report(project, stats, session)
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
