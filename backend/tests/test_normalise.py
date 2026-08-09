"""Canonical event-log tests.

The pure mapping functions are tested without a database. The two facts that
can only be established against real Postgres — that the views exist and that
the log they expose is internally consistent — use the live engine and skip
rather than fail when it is absent.

Most of these tests exist because the check they encode already caught
something on the real apache data. Where that is true the docstring says what.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.db.models import ACTIVITIES
from app.ingestion.projects import PROJECT_TO_REPO, REPO_TO_PROJECT, is_real_ticket
from app.normalise.activities import (
    CANONICAL,
    JIRA_STATUS_TO_ACTIVITY,
    REQUESTED_TO_CANONICAL,
    REVIEW_STATE_TO_ACTIVITY,
    TIMELINE_TO_ACTIVITY,
    assert_writable,
)
from app.normalise.event_log import (
    absence_reason,
    actor_from,
    case_for_pr,
    event_id_for,
    parse_ts,
    ticket_key_from_pr,
)

# --- the vocabulary translation ------------------------------------------


def test_every_requested_activity_is_accounted_for():
    """The brief names thirteen. None of them may be silently forgotten."""
    requested = {
        "commit", "pr_opened", "review_requested", "review_submitted",
        "changes_requested", "approved", "reopened", "force_push", "merged",
        "ci_started", "ci_completed", "ci_rerun", "jira_status_changed",
    }
    assert requested <= set(REQUESTED_TO_CANONICAL)


def test_every_mapped_activity_is_writable():
    """Six of the brief's names are not in the schema's CHECK constraint.
    Anything this table maps TO must be, or the INSERT fails at runtime."""
    for target, why in REQUESTED_TO_CANONICAL.values():
        assert why, "every entry must carry its reason"
        if target is not None:
            assert target in CANONICAL, f"{target} would be rejected by Postgres"


def test_unmapped_activities_explain_where_the_information_went():
    for name, (target, why) in REQUESTED_TO_CANONICAL.items():
        if target is None:
            assert len(why) > 40, f"{name} needs a real explanation, not a shrug"


def test_canonical_set_is_the_schema_set():
    """If someone edits ACTIVITIES this module must move with it."""
    assert CANONICAL == frozenset(ACTIVITIES)


@pytest.mark.parametrize("mapping", [
    JIRA_STATUS_TO_ACTIVITY, REVIEW_STATE_TO_ACTIVITY, TIMELINE_TO_ACTIVITY
])
def test_all_mapping_targets_are_in_the_vocabulary(mapping):
    for target in mapping.values():
        assert_writable(target)


def test_assert_writable_rejects_the_briefs_own_names():
    """The failure must happen here, where the message names the field, not at
    an INSERT where Postgres reports only 'violates check constraint'."""
    for name in ("pr_opened", "ci_started", "jira_status_changed"):
        with pytest.raises(ValueError, match="frozen activity vocabulary"):
            assert_writable(name)


def test_jira_open_is_a_reopen_not_a_creation():
    """ticket_created comes from fields.created exactly once. A transition
    whose target is Open is work moving backwards."""
    assert JIRA_STATUS_TO_ACTIVITY["open"] == "ticket_reopened"
    assert "ticket_created" not in JIRA_STATUS_TO_ACTIVITY.values()


def test_pending_reviews_are_not_events():
    """A PENDING review is an unsubmitted draft. No evidence, no event."""
    assert "PENDING" not in REVIEW_STATE_TO_ACTIVITY


# --- what counts as a ticket key -----------------------------------------


@pytest.mark.parametrize(("key", "repo", "ok"), [
    ("KAFKA-19871", "apache/kafka", True),
    ("FLINK-40255", "apache/flink", True),
    # Every one of these was observed in a real apache PR title, branch or body.
    ("KIP-909", "apache/kafka", False),      # improvement proposal, not an issue
    ("FLIP-187", "apache/flink", False),
    ("CVE-2026", "apache/kafka", False),
    ("GHSA-72", "apache/kafka", False),
    ("CWE-287", "apache/kafka", False),
    ("SHA-256", "apache/flink", False),      # the worst one
    ("UTF-8", "apache/flink", False),
    ("GPT-5", "apache/flink", False),
    ("CALCITE-7594", "apache/flink", False),  # a real Jira key, wrong project
    ("HADOOP-19866", "apache/kafka", False),
    # Rule 6: Kafka and Flink cases cannot collide.
    ("KAFKA-19871", "apache/flink", False),
    ("FLINK-40255", "apache/kafka", False),
])
def test_is_real_ticket(key, repo, ok):
    assert is_real_ticket(key, repo) is ok


def test_project_map_round_trips():
    for project, repo in PROJECT_TO_REPO.items():
        assert REPO_TO_PROJECT[repo] == project


# --- case identity --------------------------------------------------------


def pr_node(number=20695, title="MINOR: fix a thing", branch="patch-1", body=""):
    return {"number": number, "title": title, "headRefName": branch, "body": body}


def test_title_wins_over_branch_and_body():
    node = pr_node(
        title="KAFKA-19871: fix the thing",
        branch="KAFKA-11111-wip",
        body="see also KAFKA-22222",
    )
    assert ticket_key_from_pr(node, "apache/kafka") == ("KAFKA-19871", "title")


def test_branch_is_used_when_the_title_has_no_key():
    node = pr_node(title="MINOR: tidy up", branch="KAFKA-19871-fix")
    assert ticket_key_from_pr(node, "apache/kafka") == ("KAFKA-19871", "headRefName")


def test_a_kip_in_the_title_does_not_become_the_case():
    """KIP-909 opens 64 real Kafka PR titles. It is a design document."""
    node = pr_node(title="KIP-909: rebalance protocol")
    assert ticket_key_from_pr(node, "apache/kafka") == (None, "none")
    assert case_for_pr(node, "apache/kafka") == (
        "apache/kafka#20695", "pr", "none",
    )


def test_a_cve_in_the_body_does_not_become_the_case():
    node = pr_node(body="bumps netty, fixes CVE-2026-1234 and SHA-256 handling")
    assert case_for_pr(node, "apache/kafka")[1] == "pr"


def test_a_real_key_later_in_the_title_still_wins_over_a_junk_one_first():
    """`is_real_ticket` filters matches, so scanning must not stop at the
    first regex hit — it has to keep looking for a valid one."""
    node = pr_node(title="KIP-909 / KAFKA-19871: rebalance protocol")
    assert ticket_key_from_pr(node, "apache/kafka")[0] == "KAFKA-19871"


def test_pr_fallback_id_matches_what_git_local_emits():
    """git_local turns a subject ending `(#23015)` into `apache/kafka#23015`.
    The two must be byte-identical or a PR opens a case beside its own
    commits instead of joining them."""
    from app.ingestion.git_local import work_item_id_for

    class FakeCommit:
        sha = "a" * 40
        subject = "MINOR: fix a thing (#20695)"
        ticket_key = None
        pr_number = "20695"

    assert (
        work_item_id_for(FakeCommit(), "apache/kafka")[0]
        == case_for_pr(pr_node(20695), "apache/kafka")[0]
    )


# --- timestamps -----------------------------------------------------------


def test_parses_github_z_and_jira_offset():
    assert parse_ts("2026-03-01T10:00:00Z").tzinfo is not None
    assert parse_ts("2026-03-01T10:00:00.000+0000").year == 2026


def test_unparseable_timestamp_is_none_not_an_exception():
    """One bad timestamp must not abort a 90,000-event run."""
    assert parse_ts("not a date") is None
    assert parse_ts(None) is None
    assert parse_ts("") is None


# --- actors ---------------------------------------------------------------


def test_bots_never_become_actors():
    assert actor_from({"is_bot": True, "actor_hash": "deadbeef"}) is None
    assert absence_reason({"is_bot": True}) == "bot"


def test_a_deleted_account_is_recorded_as_unattributed():
    """GitHub returns author: null. Without a reason in attrs the report
    cannot tell this apart from a mapper that lost the author."""
    assert actor_from(None) is None
    assert absence_reason(None) == "unattributed"


def test_a_present_author_has_no_absence_reason():
    assert absence_reason({"actor_hash": "abc", "is_bot": False}) is None


# --- event identity -------------------------------------------------------


def test_event_id_is_deterministic():
    assert event_id_for("a", "b", "c") == event_id_for("a", "b", "c")


def test_two_reviewers_in_the_same_second_are_two_events():
    """Observed on apache/kafka#18253: three reviewers requested at the same
    timestamp. Without the reviewer in the id they collapse into one."""
    base = ("github_graphql", "pull_request", "apache/kafka#18253",
            "review_requested", "2024-12-18T11:10:40+00:00")
    assert event_id_for(*base, "reviewer-a") != event_id_for(*base, "reviewer-b")


# --- against live Postgres ------------------------------------------------


@pytest.fixture
def conn(pg_engine):
    with pg_engine.connect() as c:
        yield c


def _has_events(conn) -> bool:
    return bool(conn.execute(text("SELECT count(*) FROM v_event_log")).scalar())


def test_the_views_exist(conn):
    """migrations/002 has to have been applied; docs/schema.sql is frozen and
    does not contain either of these."""
    for view in ("v_event_log", "v_case_evidence"):
        assert conn.execute(
            text("SELECT to_regclass(:v)"), {"v": f"public.{view}"}
        ).scalar() is not None, f"{view} missing — apply migrations/002"


def test_the_event_log_has_the_celonis_columns(conn):
    row = conn.execute(text("SELECT * FROM v_event_log LIMIT 1")).mappings().first()
    if row is None:
        pytest.skip("event log is empty")
    for column in ("case_id", "activity", "ts", "resource", "repo",
                   "component", "sprint", "step", "in_window", "ingest_source"):
        assert column in row


def test_no_activity_outside_the_vocabulary_reached_the_table(conn):
    rogue = conn.execute(
        text("SELECT DISTINCT activity FROM event_log WHERE NOT (activity = ANY(:a))"),
        {"a": list(ACTIVITIES)},
    ).scalars().all()
    assert not rogue


def test_no_case_id_is_a_junk_ticket_key(conn):
    """`SHA-256` and `UTF-8` were work items on this database until
    is_real_ticket existed. This is the regression test for that."""
    if not _has_events(conn):
        pytest.skip("event log is empty")
    bad = conn.execute(
        text(
            """
            SELECT work_item_id, repo FROM work_item
             WHERE case_source = 'ticket_key'
               AND NOT (split_part(work_item_id, '-', 1) = ANY(:p))
            """
        ),
        {"p": list(PROJECT_TO_REPO)},
    ).all()
    assert not bad, f"invalid ticket-key cases: {bad[:5]}"


def test_no_case_spans_two_repositories(conn):
    """Rule 6. A case holding events from both kafka and flink means the
    fallback chain produced an id that is not repo-unique."""
    if not _has_events(conn):
        pytest.skip("event log is empty")
    assert conn.execute(
        text(
            """
            SELECT count(*) FROM (
                SELECT case_id FROM v_event_log WHERE attrs->>'repo' IS NOT NULL
                 GROUP BY case_id HAVING count(DISTINCT attrs->>'repo') > 1) x
            """
        )
    ).scalar() == 0


def test_every_event_has_a_timestamp(conn):
    assert conn.execute(
        text("SELECT count(*) FROM event_log WHERE ts IS NULL")
    ).scalar() == 0


def test_every_null_actor_has_a_recorded_reason(conn):
    """A null resource is fine for CI and for a bot. A null resource with no
    explanation is a mapper that dropped a human."""
    if not _has_events(conn):
        pytest.skip("event log is empty")
    assert conn.execute(
        text(
            """
            SELECT count(*) FROM v_event_log
             WHERE resource IS NULL
               AND activity NOT IN ('ci_run','deploy','ticket_created')
               AND attrs->>'actor_absent' IS NULL
            """
        )
    ).scalar() == 0


def test_commit_events_from_prs_use_git_locals_event_id_scheme(conn):
    """P2: commits fetched via the PR's own commits connection must converge
    with git_local's event_id on (sha, authored_at), or the same physical
    commit double-counts as two rows under two ids."""
    from app.ingestion.git_local import event_id_for as git_event_id

    if not _has_events(conn):
        pytest.skip("event log is empty")
    rows = conn.execute(
        text(
            """
            SELECT event_id, attrs->>'sha' AS sha, ts FROM event_log
             WHERE activity = 'commit' AND attrs->>'ingest_source' = 'github_graphql'
             LIMIT 25
            """
        )
    ).all()
    if not rows:
        pytest.skip("no PR-sourced commit events mapped yet")
    for event_id, sha, ts in rows:
        assert event_id == git_event_id(sha, ts), (sha, ts)


def test_case_sequence_is_deterministic_and_dense(conn):
    """`step` must be 1..n with no gaps, or the ordering tiebreak is unstable
    and two runs of the variant miner disagree."""
    if not _has_events(conn):
        pytest.skip("event log is empty")
    assert conn.execute(
        text(
            """
            SELECT count(*) FROM (
                SELECT case_id, count(*) n, max(step) m
                  FROM v_event_log GROUP BY 1 HAVING count(*) <> max(step)) x
            """
        )
    ).scalar() == 0


def test_no_event_belongs_to_a_missing_case(conn):
    """v_event_log inner-joins work_item, so a dangling event would vanish
    from the log silently rather than erroring."""
    assert conn.execute(
        text("SELECT count(*) FROM v_event_log")
    ).scalar() == conn.execute(text("SELECT count(*) FROM event_log")).scalar()


def test_no_identity_reached_the_event_log(conn):
    """The whole privacy claim, asserted against the real table."""
    if not _has_events(conn):
        pytest.skip("event log is empty")
    leaked = conn.execute(
        text(
            """
            SELECT count(*) FROM event_log
             WHERE attrs::text ~* '(@[a-z0-9.-]+\\.[a-z]{2,}|"login"|"displayName"|"emailAddress")'
            """
        )
    ).scalar()
    assert leaked == 0


def test_the_app_role_can_read_the_new_views(pg_engine):
    """The API reads views only. An ungranted view is a 500 at demo time."""
    from app.db.session import get_read_engine

    with get_read_engine().connect() as conn:
        for view in ("v_event_log", "v_case_evidence"):
            conn.execute(text(f"SELECT 1 FROM {view} LIMIT 1"))


def test_rerunning_the_mapper_does_not_duplicate_events(pg_engine):
    """Idempotency, proved rather than asserted. Every event id is a hash of
    its own evidence, so a second pass must upsert in place.

    Mapped twice and compared between the two, never against a count taken
    before the first — an ingestion run in another terminal lands payloads
    continuously, and comparing across the first pass measures the fetch
    rather than the mapper. The payload count is re-read either side and the
    test skips if it moved, so a false pass is not possible either.
    """
    from app.db.session import write_session
    from app.normalise.event_log import Stats, map_jira, map_pull_requests

    def counts(conn):
        return (
            conn.execute(text("SELECT count(*) FROM event_log")).scalar(),
            conn.execute(text("SELECT count(*) FROM raw_payload")).scalar(),
        )

    def remap():
        with write_session() as session:
            map_pull_requests(session, Stats())
            map_jira(session, Stats())

    remap()
    with pg_engine.connect() as conn:
        events_1, payloads_1 = counts(conn)
    if not events_1:
        pytest.skip("event log is empty")

    remap()
    with pg_engine.connect() as conn:
        events_2, payloads_2 = counts(conn)

    if payloads_2 != payloads_1:
        pytest.skip("ingestion is landing payloads concurrently")
    assert events_2 == events_1


def test_ci_events_never_outnumber_the_runs_behind_them(conn):
    """Upsert alone leaves an event behind when its ci_run row goes away.
    That happened: 53,580 CI events survived a table that had been emptied,
    so the log claimed more CI activity than the CI table contained.

    Only one direction is a defect. A newly fetched run that the normaliser
    has not mapped yet is normal and happens whenever ingestion is mid-flight;
    an event with no run behind it is not.
    """
    if not _has_events(conn):
        pytest.skip("event log is empty")
    orphaned = conn.execute(
        text(
            """
            SELECT count(*) FROM event_log e
             WHERE e.activity = 'ci_run'
               AND NOT EXISTS (SELECT 1 FROM ci_run c
                                WHERE c.run_id = e.attrs->>'run_id'
                                  AND c.work_item_id IS NOT NULL)
            """
        )
    ).scalar()
    assert orphaned == 0, f"{orphaned} CI events have no surviving run"


# --- epic -----------------------------------------------------------------


def test_epic_comes_from_the_improvement_proposal():
    """Apache uses GitHub milestones exactly zero times across 5,632 PRs, so
    milestone.title populates nothing. A KIP is the real epic: a named body of
    work spanning many PRs over months."""
    from app.ingestion.projects import epic_from_text

    assert epic_from_text("KIP-1071: new rebalance protocol") == "KIP-1071"
    assert epic_from_text("MINOR: tidy", "implements FLIP-187") == "FLIP-187"
    assert epic_from_text("KAFKA-19871: fix a thing") is None


def test_the_epic_and_the_case_id_are_not_the_same_thing():
    """KIP-1071 must never become a case — is_real_ticket rejects it — but it
    must become that case's epic. Both, from the same string."""
    from app.ingestion.projects import epic_from_text, is_real_ticket

    title = "KIP-1071: new rebalance protocol"
    assert is_real_ticket("KIP-1071", "apache/kafka") is False
    assert epic_from_text(title) == "KIP-1071"
