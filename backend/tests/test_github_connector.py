"""
GitHub connector tests.

No token and no network: a fake GitHub is served over httpx.MockTransport, so
paging, cursors, retry, backoff and — most importantly — identity scrubbing all
execute for real against realistic response shapes.

The scrubbing tests are the ones that matter. Everything else here fails
loudly in a demo; a login reaching Postgres fails silently and permanently.
"""

from __future__ import annotations

import json
import time
from datetime import datetime

import httpx
import pytest

from app.ingestion.github_connector import (
    MAX_PAGE_SIZE,
    PR_QUERY,
    RUN_FIELDS,
    GitHubClient,
    GitHubError,
    MissingToken,
    RateLimit,
    RateLimiter,
    Stats,
    _assert_scrubbed,
    _count_timeline,
    collect_logins,
    keep_run_fields,
    redact_text,
    scrub_actor,
    scrub_payload,
    wall_clock_minutes,
)

#: The DB-touching tests write under their own repo name.
#:
#: They used to use `apache/kafka` and assert on unfiltered counts — `SELECT
#: count(*) FROM ci_run` — which only worked while the table was empty, and
#: which forced the fixtures to truncate real tables to keep it that way. A
#: namespace no ingestion run will ever produce means the test rows and the
#: real rows cannot see each other, and no cleanup has to reach outside it.
TEST_REPO = "esi-test/fixture-repo"


class FakeClock:
    """Deterministic time so backoff and windows are testable without sleeping."""

    def __init__(self) -> None:
        self.t = 0.0
        self.slept: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.t += seconds


def pr_node(number: int = 1, login: str = "octocat", typename: str = "User") -> dict:
    return {
        "number": number,
        "title": f"KAFKA-{number}: fix a thing",
        "body": "cc @octocat and someone@apache.org, see KAFKA-99",
        "headRefName": f"octocat/kafka-{number}",
        "createdAt": "2026-03-01T10:00:00Z",
        "updatedAt": "2026-03-02T10:00:00Z",
        "mergedAt": "2026-03-02T10:00:00Z",
        "closedAt": "2026-03-02T10:00:00Z",
        "changedFiles": 2,
        "additions": 10,
        "deletions": 3,
        "mergeCommit": {"oid": "abc123"},
        "author": {"login": login, "__typename": typename},
        "files": {"nodes": [{"path": "core/A.java"}, {"path": "core/B.java"}]},
        "reviews": {
            "nodes": [
                {
                    "state": "APPROVED",
                    "submittedAt": "2026-03-02T09:00:00Z",
                    "author": {"login": "reviewer1", "__typename": "User"},
                }
            ]
        },
        "timelineItems": {
            "nodes": [
                {
                    "__typename": "ReviewRequestedEvent",
                    "createdAt": "2026-03-01T11:00:00Z",
                    "actor": {"login": login, "__typename": "User"},
                    "requestedReviewer": {"login": "reviewer1"},
                },
                {
                    "__typename": "HeadRefForcePushedEvent",
                    "createdAt": "2026-03-01T12:00:00Z",
                    "actor": {"login": login, "__typename": "User"},
                },
            ]
        },
    }


def graphql_page(nodes, has_next=False, end_cursor="CUR1", cost=1, remaining=4999):
    return {
        "data": {
            "rateLimit": {
                "limit": 5000,
                "cost": cost,
                "remaining": remaining,
                "resetAt": "2026-03-01T12:00:00Z",
                "nodeCount": 100,
            },
            "repository": {
                "pullRequests": {
                    "pageInfo": {"hasNextPage": has_next, "endCursor": end_cursor},
                    "nodes": nodes,
                }
            },
        }
    }


def make_client(handler, clock: FakeClock | None = None) -> GitHubClient:
    clock = clock or FakeClock()
    return GitHubClient(
        token="ghp_fake",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        limiter=RateLimiter(sleep=clock.sleep, now=clock.now),
    )


# --- token ---------------------------------------------------------------


@pytest.mark.parametrize("token", ["", "   ", None])
def test_missing_token_is_refused_with_an_actionable_message(token):
    """GraphQL v4 has no anonymous tier, unlike REST's 60/hr."""
    with pytest.raises(MissingToken) as excinfo:
        GitHubClient(token=token)
    assert "GITHUB_TOKEN" in str(excinfo.value)


def test_token_is_sent_as_a_bearer_header():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=graphql_page([]))

    make_client(handler).execute(PR_QUERY, {})
    assert seen["auth"] == "bearer ghp_fake"


# --- identity scrubbing --------------------------------------------------


def test_author_login_becomes_an_actor_hash():
    scrubbed = scrub_actor({"login": "octocat", "__typename": "User"})
    assert "login" not in scrubbed
    assert len(scrubbed["actor_hash"]) == 16
    assert scrubbed["is_bot"] is False


def test_bots_are_marked_and_never_hashed():
    """A hashed bot would enter the identity store and inflate 'how many
    humans contributed' for every later count."""
    scrubbed = scrub_actor({"login": "dependabot[bot]", "__typename": "Bot"})
    assert scrubbed["is_bot"] is True
    assert "actor_hash" not in scrubbed
    assert "login" not in scrubbed


def test_typename_bot_is_honoured_even_for_an_unknown_name():
    assert scrub_actor({"login": "mystery", "__typename": "Bot"})["is_bot"] is True


def test_scrub_reaches_logins_at_every_depth():
    body = scrub_payload(pr_node())
    _assert_scrubbed(body)  # raises if any survived
    assert "octocat" not in json.dumps(body)
    assert "reviewer1" not in json.dumps(body)


def test_assert_scrubbed_catches_a_surviving_login():
    """The last gate. If scrub_payload ever regresses, this is what fires."""
    with pytest.raises(AssertionError, match="identity key"):
        _assert_scrubbed({"a": {"b": [{"login": "octocat"}]}})


def test_assert_scrubbed_passes_clean_payloads():
    _assert_scrubbed({"actor_hash": "ab12", "nodes": [{"path": "core/A.java"}]})


def test_email_in_a_pr_body_is_redacted():
    assert "someone@apache.org" not in redact_text("ping someone@apache.org now")


def test_mention_in_a_pr_body_is_hashed_not_left_as_a_login():
    out = redact_text("cc @octocat please")
    assert "@octocat" not in out
    assert out.startswith("cc @")


def test_ticket_keys_survive_redaction():
    """Decision #6 extracts the case id from the body — redaction must not
    destroy the thing the body is stored for."""
    assert "KAFKA-16234" in redact_text("fixes KAFKA-16234, cc @octocat")


def test_file_paths_are_not_mangled_by_mention_redaction():
    body = scrub_payload(pr_node())
    assert [f["path"] for f in body["files"]["nodes"]] == [
        "core/A.java",
        "core/B.java",
    ]


# --- rate limiting -------------------------------------------------------


def test_minimum_interval_is_enforced_between_requests():
    """The 90s-CPU-per-60s secondary limit is unobservable; spacing is the
    only defence we have against it."""
    clock = FakeClock()
    limiter = RateLimiter(sleep=clock.sleep, now=clock.now, min_interval_s=0.75)
    limiter.before_request()
    limiter.before_request()
    assert clock.slept and clock.slept[0] == pytest.approx(0.75)


def test_secondary_points_per_minute_limit_triggers_a_sleep():
    clock = FakeClock()
    limiter = RateLimiter(
        points_per_minute=10, sleep=clock.sleep, now=clock.now, min_interval_s=0
    )
    for _ in range(10):
        limiter.after_response(RateLimit(cost=1, remaining=4999))
    assert limiter.points_in_window == 10
    limiter.before_request()
    assert any(s > 0 for s in clock.slept)


def test_points_leave_the_window_after_sixty_seconds():
    clock = FakeClock()
    limiter = RateLimiter(sleep=clock.sleep, now=clock.now)
    limiter.after_response(RateLimit(cost=5, remaining=4999))
    clock.t += 61
    assert limiter.points_in_window == 0


def test_primary_budget_exhaustion_sleeps_until_reset():
    clock = FakeClock()
    limiter = RateLimiter(sleep=clock.sleep, now=clock.now, primary_floor=100)
    limiter.after_response(
        RateLimit(cost=1, remaining=5, reset_at="2099-01-01T00:00:00Z")
    )
    assert clock.slept and clock.slept[-1] > 0


def test_backoff_is_jittered_and_bounded():
    clock = FakeClock()
    limiter = RateLimiter(sleep=clock.sleep, now=clock.now)
    delays = [limiter.backoff(attempt=5) for _ in range(20)]
    assert all(0 <= d <= 60 for d in delays)
    assert len(set(delays)) > 1, "no jitter: synchronised retries cause bans"


def test_retry_after_header_wins_over_computed_backoff():
    clock = FakeClock()
    limiter = RateLimiter(sleep=clock.sleep, now=clock.now)
    assert limiter.backoff(attempt=1, retry_after=17.0) == 17.0


# --- retry behaviour -----------------------------------------------------


def test_403_is_retried_then_succeeds():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(403, headers={"retry-after": "1"}, json={})
        return httpx.Response(200, json=graphql_page([]))

    data = make_client(handler).execute(PR_QUERY, {})
    assert calls["n"] == 2
    assert data["rateLimit"]["remaining"] == 4999


def test_graphql_rate_limited_error_is_retried():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json={"errors": [{"type": "RATE_LIMITED"}]})
        return httpx.Response(200, json=graphql_page([]))

    make_client(handler).execute(PR_QUERY, {})
    assert calls["n"] == 2


def test_other_graphql_errors_fail_fast():
    """A malformed query must not burn six retries and two minutes."""

    def handler(request):
        return httpx.Response(200, json={"errors": [{"message": "Bad field"}]})

    with pytest.raises(GitHubError, match="GraphQL errors"):
        make_client(handler).execute(PR_QUERY, {})


def test_server_errors_are_retried_then_give_up():
    def handler(request):
        return httpx.Response(502, json={})

    with pytest.raises(GitHubError, match="giving up"):
        make_client(handler).execute(PR_QUERY, {})


# --- query shape ---------------------------------------------------------


def test_review_requested_events_are_in_the_query():
    """Non-negotiable: without these there is no review-latency metric."""
    assert "REVIEW_REQUESTED_EVENT" in PR_QUERY
    assert "ReviewRequestedEvent" in PR_QUERY


def test_all_required_timeline_item_types_are_requested():
    for item in (
        "REVIEW_REQUESTED_EVENT",
        "REOPENED_EVENT",
        "HEAD_REF_FORCE_PUSHED_EVENT",
    ):
        assert item in PR_QUERY


def test_every_connection_is_bounded_between_1_and_100():
    """GitHub rejects an unbounded connection and does not say which one."""
    import re

    firsts = [int(n) for n in re.findall(r"first:\s*(\d+)", PR_QUERY)]
    assert firsts, "no bounded connections found"
    assert all(1 <= n <= MAX_PAGE_SIZE for n in firsts)
    # files/reviews/timelineItems all bounded, plus the paged $pageSize.
    assert "first: $pageSize" in PR_QUERY


def test_rate_limit_is_requested_on_every_response():
    assert "rateLimit" in PR_QUERY


def test_query_requests_the_fields_the_mapper_needs():
    for fieldname in (
        "number",
        "headRefName",
        "mergedAt",
        "mergeCommit",
        "changedFiles",
        "updatedAt",
    ):
        assert fieldname in PR_QUERY


# --- counting ------------------------------------------------------------


def test_timeline_counting():
    stats = Stats()
    _count_timeline(pr_node(), stats)
    assert stats.review_requested_events == 1
    assert stats.force_pushes == 1
    assert stats.reviews == 1
    assert stats.reopened == 0


def test_timeline_counting_survives_missing_sections():
    stats = Stats()
    _count_timeline({"number": 1}, stats)
    assert stats.review_requested_events == 0


# --- bare logins in branch names -----------------------------------------


def test_fork_branch_name_does_not_leak_the_author_login():
    """GitHub names a fork's branch after its owner: headRefName arrives as
    'octocat/kafka-1'. No '@', no 'login' key — it would sail straight into
    Postgres. This is the leak the first version of this module shipped with."""
    body = scrub_payload(pr_node(login="octocat"))
    assert "octocat" not in body["headRefName"]
    assert "kafka-1" in body["headRefName"]


def test_reviewer_login_is_redacted_from_free_text_too():
    """The PR author is rarely the only person named in a PR."""
    node = pr_node(login="alice")
    node["body"] = "reviewer1 should look at this"
    body = scrub_payload(node)
    assert "reviewer1" not in body["body"]


def test_collect_logins_finds_every_login_in_a_node():
    assert collect_logins(pr_node(login="alice")) == {"alice", "reviewer1"}


def test_redaction_is_case_insensitive():
    """GitHub logins are case-insensitive; 'OctoCat/branch' is the same person."""
    assert "octocat" not in redact_text("OctoCat/patch-1", ["octocat"]).lower()


def test_redaction_does_not_mangle_an_unrelated_substring():
    """'alice' must not be clipped out of 'alicedoc'."""
    assert redact_text("alicedoc/readme", ["alice"]) == "alicedoc/readme"


def test_no_login_survives_anywhere_in_a_full_node():
    body = scrub_payload(pr_node(login="octocat"))
    blob = json.dumps(body)
    assert "octocat" not in blob
    assert "reviewer1" not in blob
    _assert_scrubbed(body)


# --- paging, cursors and the window (live Postgres) ----------------------


@pytest.fixture
def clean_github_rows(pg_engine):
    """Remove only the rows THIS TEST wrote.

    The earlier version deleted every github_graphql row in raw_payload, on
    the assumption that a test database is a scratch database. DATABASE_URL
    points at the real one, so running the suite destroyed 5,632 fetched PR
    payloads and cost a re-fetch. A test may not delete data it did not
    create; the fixture snapshots what was already there and removes the
    difference.
    """
    from sqlalchemy import text

    def _clean():
        with pg_engine.begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM raw_payload WHERE source='github_graphql' "
                    "AND entity_id = ANY(:keys)"
                ),
                {"keys": [f"{TEST_REPO}#{n}" for n in range(1, 200)]},
            )
            conn.execute(
                text(
                    "DELETE FROM ingest_cursor WHERE source='github_graphql' "
                    "AND scope = ANY(:keys)"
                ),
                {"keys": [TEST_REPO]},
            )

    _clean()
    yield
    _clean()


def _fetch(handler, **kw):
    from app.db.session import write_session
    from app.ingestion.github_connector import fetch_repo

    with write_session() as session:
        return fetch_repo(TEST_REPO, make_client(handler), session, **kw)


def test_paging_follows_the_cursor_and_lands_every_row(clean_github_rows):
    pages = [
        graphql_page([pr_node(1), pr_node(2)], has_next=True, end_cursor="C1"),
        graphql_page([pr_node(3)], has_next=False, end_cursor="C2"),
    ]
    seen_cursors = []

    def handler(request):
        body = json.loads(request.content)
        seen_cursors.append(body["variables"]["cursor"])
        return httpx.Response(200, json=pages[len(seen_cursors) - 1])

    stats = _fetch(handler)
    assert stats.pages == 2
    assert stats.pull_requests == 3
    assert seen_cursors == [None, "C1"], "second page did not use the first's cursor"


def test_review_requested_events_survive_the_round_trip(clean_github_rows):
    """The headline waste metric depends on these existing in raw_payload."""

    def handler(request):
        return httpx.Response(200, json=graphql_page([pr_node(1), pr_node(2)]))

    stats = _fetch(handler)
    assert stats.review_requested_events == 2
    assert stats.reviews == 2


def test_cursor_is_cleared_after_a_completed_run(clean_github_rows):
    """A stale cursor on a UPDATED_AT DESC feed would skip new activity."""
    from app.db.session import write_session
    from app.ingestion.github_connector import load_cursor

    def handler(request):
        return httpx.Response(200, json=graphql_page([pr_node(1)]))

    _fetch(handler)
    with write_session() as session:
        assert load_cursor(session, TEST_REPO) is None


def test_an_interrupted_run_leaves_a_resume_cursor(clean_github_rows):
    from app.db.session import write_session
    from app.ingestion.github_connector import load_cursor

    def handler(request):
        return httpx.Response(
            200, json=graphql_page([pr_node(1)], has_next=True, end_cursor="C1")
        )

    _fetch(handler, max_pages=1)
    with write_session() as session:
        assert load_cursor(session, TEST_REPO) == "C1"


def test_paging_stops_at_the_history_window(clean_github_rows):
    old = pr_node(9)
    old["updatedAt"] = "2019-01-01T00:00:00Z"

    def handler(request):
        return httpx.Response(
            200, json=graphql_page([pr_node(1), old], has_next=True, end_cursor="C1")
        )

    stats = _fetch(handler)
    assert stats.stopped_on_window
    assert stats.pull_requests == 1, "the out-of-window PR should not be landed"


def _github_row_count() -> int:
    from sqlalchemy import text

    from app.db.session import get_write_engine

    with get_write_engine().connect() as conn:
        return conn.execute(
            text(
                "SELECT count(*) FROM raw_payload WHERE source='github_graphql' "
                "AND entity_id LIKE :prefix"
            ),
            {"prefix": f"{TEST_REPO}#%"},
        ).scalar_one()


def test_refetch_is_idempotent(clean_github_rows):
    """Upsert on (source, entity_type, entity_id): a re-run updates, never
    duplicates. This is what makes clearing the cursor safe."""

    def handler(request):
        return httpx.Response(200, json=graphql_page([pr_node(1), pr_node(2)]))

    _fetch(handler)
    assert _github_row_count() == 2
    _fetch(handler)
    assert _github_row_count() == 2


def test_no_login_reaches_postgres(clean_github_rows):
    """The claim, verified against the database rather than the object."""
    from sqlalchemy import text

    from app.db.session import get_write_engine

    def handler(request):
        return httpx.Response(200, json=graphql_page([pr_node(1, login="octocat")]))

    _fetch(handler)
    with get_write_engine().connect() as conn:
        blob = conn.execute(
            text(
                "SELECT body::text FROM raw_payload WHERE source='github_graphql' "
                "AND entity_id LIKE :prefix"
            ),
            {"prefix": f"{TEST_REPO}#%"},
        ).scalar_one()
    assert "octocat" not in blob
    assert "reviewer1" not in blob
    assert '"login"' not in blob


@pytest.mark.parametrize("size", [0, 101, -1])
def test_page_size_must_be_between_1_and_100(size, clean_github_rows):
    """Every GraphQL connection needs first/last in 1..100."""

    def handler(request):
        return httpx.Response(200, json=graphql_page([]))

    with pytest.raises(ValueError, match="page_size"):
        _fetch(handler, page_size=size)


def test_repo_must_be_owner_slash_name(clean_github_rows):
    from app.db.session import write_session
    from app.ingestion.github_connector import fetch_repo

    def handler(request):
        return httpx.Response(200, json=graphql_page([]))

    with write_session() as session, pytest.raises(ValueError, match="owner/name"):
        fetch_repo("kafka", make_client(handler), session)


# --- Actions runs --------------------------------------------------------


def run_node(
    run_id=1,
    sha="abc",
    attempt=1,
    conclusion="success",
    started="2026-03-01T10:00:00Z",
    updated="2026-03-01T10:12:30Z",
):
    """A workflow run as GitHub returns it, including the parts we discard."""
    return {
        "id": run_id,
        "head_sha": sha,
        "conclusion": conclusion,
        "run_started_at": started,
        "updated_at": updated,
        "run_attempt": attempt,
        "name": "CI",
        # Everything below is real API noise that must NOT reach Postgres.
        "head_commit": {"author": {"name": "Ada Lovelace", "email": "ada@apache.org"}},
        "actor": {"login": "octocat"},
        "repository": {"full_name": "apache/kafka", "owner": {"login": "apache"}},
    }


def test_run_projection_drops_the_identity_bearing_fields():
    """head_commit.author carries a real name AND email. The projection is a
    privacy control, not just a size optimisation."""
    kept = keep_run_fields(run_node())
    assert set(kept) == (set(RUN_FIELDS) - {"name"}) | {"workflow_name"}
    assert kept["workflow_name"] == "CI"
    assert "head_commit" not in kept
    assert "actor" not in kept
    assert "Ada Lovelace" not in json.dumps(kept)
    assert "ada@apache.org" not in json.dumps(kept)


def test_wall_clock_minutes():
    assert wall_clock_minutes(run_node()) == pytest.approx(12.5)


def test_wall_clock_is_never_negative():
    """A run whose updated_at precedes run_started_at would otherwise produce
    a negative cost."""
    run = run_node(started="2026-03-01T10:00:00Z", updated="2026-03-01T09:00:00Z")
    assert wall_clock_minutes(run) == 0.0


def test_wall_clock_handles_a_run_that_never_started():
    assert wall_clock_minutes(run_node(started=None)) == 0.0


def test_rerun_is_detected_from_run_attempt():
    assert run_node(attempt=2)["run_attempt"] > 1


# --- Actions against live Postgres ---------------------------------------


@pytest.fixture
def clean_actions_rows(pg_engine):
    """Same rule as clean_github_rows, and this one was worse: it ran a bare
    `DELETE FROM ci_run`, which emptied 79,085 real rows every time the suite
    ran. Snapshot, then remove only the difference."""
    from sqlalchemy import text

    def _clean():
        with pg_engine.begin() as conn:
            conn.execute(
                text("DELETE FROM ci_run WHERE repo = ANY(:keys)"),
                {"keys": [TEST_REPO]},
            )
            conn.execute(
                text(
                    "DELETE FROM raw_payload WHERE source='github_actions' "
                    "AND entity_id = ANY(:keys)"
                ),
                {"keys": [f"{TEST_REPO}#{n}" for n in range(200)]},
            )

    _clean()
    yield
    _clean()


def _actions_response(runs, total=None):
    return {
        "total_count": total if total is not None else len(runs),
        "workflow_runs": runs,
    }


def _fetch_actions(handler, **kw):
    from app.db.session import write_session
    from app.ingestion.github_connector import fetch_actions_runs

    with write_session() as session:
        return fetch_actions_runs(TEST_REPO, make_client(handler), session, **kw)


def test_actions_runs_land_in_ci_run(clean_actions_rows):
    def handler(request):
        return httpx.Response(
            200,
            json=_actions_response([run_node(1), run_node(2, attempt=2)]),
            headers={"x-ratelimit-remaining": "4990"},
        )

    stats = _fetch_actions(handler)
    assert stats.runs == 2
    assert stats.reruns == 1
    assert stats.total_minutes == pytest.approx(25.0)
    assert stats.rerun_minutes == pytest.approx(12.5)


def test_created_filter_bounds_the_window(clean_actions_rows):
    seen = {}

    def handler(request):
        seen["created"] = dict(request.url.params).get("created")
        seen["per_page"] = dict(request.url.params).get("per_page")
        return httpx.Response(200, json=_actions_response([]))

    _fetch_actions(handler)
    # A range, not ">=SINCE": the pager bisects windows to get under GitHub's
    # 1,000-result pagination cap, so every request names both ends.
    since, sep, until = seen["created"].partition("..")
    assert sep == "..", f"expected a date range, got {seen['created']!r}"
    assert datetime.fromisoformat(since) < datetime.fromisoformat(until)
    assert seen["per_page"] == "100"


def test_timing_endpoint_is_never_called(clean_actions_rows):
    """One request per run would eat the entire REST budget."""
    paths = []

    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(200, json=_actions_response([run_node(1)]))

    _fetch_actions(handler)
    assert not any("timing" in p for p in paths)
    assert all(p.endswith("/actions/runs") for p in paths)


def test_duration_basis_is_recorded_in_run_config(clean_actions_rows):
    """The disclosure lives in the database, so it survives the demo."""
    from sqlalchemy import text

    from app.db.session import get_write_engine

    def handler(request):
        return httpx.Response(200, json=_actions_response([run_node(1)]))

    _fetch_actions(handler)
    with get_write_engine().connect() as conn:
        value, note = conn.execute(
            text("SELECT value, note FROM run_config WHERE key='ci_duration_basis'")
        ).one()
    assert value == "wall_clock"
    assert "billable" in note


def test_run_joins_to_a_work_item_when_the_sha_is_known(clean_actions_rows):
    """git_local stores each commit's sha in event_log.attrs."""
    from sqlalchemy import text

    from app.db.session import get_write_engine

    with get_write_engine().connect() as conn:
        known_sha = conn.execute(
            text("SELECT attrs->>'sha' FROM event_log WHERE activity='commit' LIMIT 1")
        ).scalar_one()

    def handler(request):
        return httpx.Response(
            200, json=_actions_response([run_node(1, sha=known_sha), run_node(2)])
        )

    stats = _fetch_actions(handler)
    assert stats.mapped == 1, "the known sha should have joined to its case"


def test_unmatched_runs_keep_a_null_work_item(clean_actions_rows):
    """Most runs do not map and that is fine — they must not be dropped."""
    from sqlalchemy import text

    from app.db.session import get_write_engine

    def handler(request):
        return httpx.Response(
            200, json=_actions_response([run_node(1, sha="no-such-sha")])
        )

    stats = _fetch_actions(handler)
    assert stats.runs == 1
    assert stats.mapped == 0
    with get_write_engine().connect() as conn:
        assert (
            conn.execute(
                text("SELECT work_item_id FROM ci_run WHERE head_sha='no-such-sha'")
            ).scalar_one()
            is None
        )


def test_actions_refetch_is_idempotent(clean_actions_rows):
    from sqlalchemy import text

    from app.db.session import get_write_engine

    def handler(request):
        return httpx.Response(200, json=_actions_response([run_node(1), run_node(2)]))

    _fetch_actions(handler)
    _fetch_actions(handler)
    with get_write_engine().connect() as conn:
        assert conn.execute(
            text("SELECT count(*) FROM ci_run WHERE repo = :r"), {"r": TEST_REPO}
        ).scalar_one() == 2


def test_no_identity_from_actions_reaches_postgres(clean_actions_rows):
    from sqlalchemy import text

    from app.db.session import get_write_engine

    def handler(request):
        return httpx.Response(200, json=_actions_response([run_node(1)]))

    _fetch_actions(handler)
    with get_write_engine().connect() as conn:
        blob = conn.execute(
            text(
                "SELECT body::text FROM raw_payload WHERE source='github_actions' "
                "AND entity_id LIKE :prefix"
            ),
            {"prefix": f"{TEST_REPO}#%"},
        ).scalar_one()
    for leak in ("Ada Lovelace", "ada@apache.org", "octocat", '"login"'):
        assert leak not in blob


def test_dense_window_is_bisected_rather_than_truncated(clean_actions_rows):
    """GitHub reports 103,888 runs for apache/kafka but paginates only 1,000.

    A flat pager collects the most recent 1% and calls it a year: the rows are
    real, there are just almost none for any sprint older than a fortnight.
    Each window must therefore be split until it fits under the cap.
    """
    windows = []

    def handler(request):
        created = dict(request.url.params)["created"]
        since, _, until = created.partition("..")
        windows.append((since, until))
        span_days = (datetime.fromisoformat(until) - datetime.fromisoformat(since)).days
        # Wide windows look dense; narrow ones fit under the cap.
        if span_days > 20:
            return httpx.Response(
                200,
                json=_actions_response([], total=50_000),
                headers={"x-ratelimit-remaining": "4000"},
            )
        return httpx.Response(
            200,
            json=_actions_response([run_node(1)], total=1),
            headers={"x-ratelimit-remaining": "4000"},
        )

    stats = _fetch_actions(handler)
    assert len(windows) > 1, "the 12-month window was never split"
    spans = [
        (datetime.fromisoformat(u) - datetime.fromisoformat(s)).days for s, u in windows
    ]
    assert min(spans) <= 20, "bisection did not reach a window under the cap"
    assert not stats.hit_pagination_cap
    assert stats.runs > 0


def test_bisection_terminates_at_a_single_day(clean_actions_rows):
    """A day with >1,000 runs cannot be split further. It must stop and say
    the number is a floor, not loop forever."""

    def handler(request):
        return httpx.Response(
            200,
            json=_actions_response([run_node(1)], total=50_000),
            headers={"x-ratelimit-remaining": "4000"},
        )

    stats = _fetch_actions(handler)
    assert stats.hit_pagination_cap
    assert stats.unreachable > 0


def test_rest_budget_exhaustion_sleeps(clean_actions_rows):
    clock = FakeClock()
    limiter = RateLimiter(sleep=clock.sleep, now=clock.now, primary_floor=100)
    limiter.after_rest_response(
        httpx.Headers(
            {
                "x-ratelimit-remaining": "3",
                "x-ratelimit-reset": str(int(time.time()) + 30),
            }
        )
    )
    assert clock.slept and clock.slept[-1] > 0


def test_workflow_name_is_stored_under_an_unambiguous_key():
    """A bare `name` key is what a PERSON's name arrives under, so the scrub
    guard rejects it. The workflow name is renamed rather than exempted."""
    kept = keep_run_fields(run_node())
    assert "name" not in kept
    _assert_scrubbed(kept)


# --- labels, milestone, mergedBy -----------------------------------------


def test_labels_become_plain_strings_so_no_name_key_survives():
    """A GitHub label is {"name": "core"} and a person is {"name": "Ada"}.
    The guard cannot tell them apart, so the shape is removed rather than the
    guard weakened — same fix as workflow_name in the Actions projection."""
    from app.ingestion.github_connector import flatten_labels

    labels = flatten_labels({"nodes": [{"name": "core"}, {"name": "streams"}]})
    assert labels == ["core", "streams"]
    _assert_scrubbed({"labels": labels})


def test_a_pr_with_labels_and_a_milestone_passes_the_guard():
    """This is the regression that kept work_item.epic permanently null."""
    node = pr_node()
    node["labels"] = {"nodes": [{"name": "core"}, {"name": "KIP"}]}
    node["milestone"] = {"title": "4.0.0", "number": 12}
    body = scrub_payload(node)
    _assert_scrubbed(body)
    assert body["labels"] == ["core", "KIP"]
    assert body["milestone"]["title"] == "4.0.0"


@pytest.mark.parametrize("field", ["milestone", "labels", "mergedBy"])
def test_the_query_asks_for_the_fields_epic_and_attribution_need(field):
    """work_item.epic stays null forever if the query never asks for the
    milestone, and a merge has no actor if it never asks who merged it."""
    assert field in PR_QUERY, f"PR_QUERY is missing {field}"


def test_every_bounded_connection_in_the_query_has_a_page_size():
    """GitHub rejects any unbounded connection outright, and the error does
    not name the offending one — labels was the newest way to trip this."""
    import re

    for connection in re.findall(r"(\w+)\(([^)]*)\)", PR_QUERY):
        name, args = connection
        if name in ("pullRequests", "files", "reviews", "timelineItems", "labels"):
            assert "first:" in args, f"{name} has no page size"


def test_merged_by_is_scrubbed_like_any_other_actor():
    node = pr_node()
    node["mergedBy"] = {"login": "committer1", "__typename": "User"}
    body = scrub_payload(node)
    _assert_scrubbed(body)
    assert body["mergedBy"]["actor_hash"]
    assert "committer1" not in json.dumps(body)


def test_a_label_literally_named_like_a_person_still_cannot_leak_a_key():
    """Even a hostile label name is only ever a string in a list."""
    from app.ingestion.github_connector import flatten_labels

    _assert_scrubbed({"labels": flatten_labels({"nodes": [{"name": "Ada Lovelace"}]})})
