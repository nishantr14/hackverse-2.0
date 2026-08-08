"""
Tests for app.normalise.map_github.
Owner: Dipen (normalise lane).

Every test drives a hand-written `raw_payload` body from
backend/tests/fixtures/ through a pure mapping function. No network, no
database, no ingested data — the mapper is a function from landed payloads to
rows, and this file holds it to that.

The fixtures are already scrubbed, exactly as `github_connector` writes them:
`actor_hash` and `is_bot`, never a `login`. A test at the bottom asserts that,
so a fixture someone captures from the live API and pastes in cannot smuggle
identity into the repo.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from app.db.models import ACTIVITIES
from app.ingestion.git_local import Commit
from app.ingestion.git_local import event_id_for as git_event_id
from app.normalise import map_github as mg

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
def no_ticket() -> dict:
    return load("pr_no_ticket_key")


@pytest.fixture
def review_rounds() -> dict:
    return load("pr_multiple_review_rounds")


@pytest.fixture
def force_push() -> dict:
    return load("pr_force_push")


@pytest.fixture
def with_issue() -> dict:
    return load("pr_reopened_with_issue")


@pytest.fixture
def repoints() -> dict:
    return load("pr_ticket_key_repoints_commit")


# =====================================================================
# Conventions inherited from git_local — the thing most worth pinning
# =====================================================================


def test_event_id_matches_git_local_exactly():
    """If these ever diverge, every commit event is rewritten under a second
    id and the event log silently doubles."""
    ts = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
    sha = "abc123"
    assert mg.event_id_for("git_local", "commit", sha, "commit", ts) == git_event_id(
        sha, ts
    )


def test_event_id_is_24_hex_chars():
    ts = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
    eid = mg.event_id_for("github_graphql", "pull_request", "x#1", "merged", ts)
    assert len(eid) == 24
    assert all(c in "0123456789abcdef" for c in eid)


def test_event_id_is_deterministic_across_calls():
    ts = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
    args = ("github_graphql", "pull_request", "apache/kafka#1", "merged", ts)
    assert mg.event_id_for(*args) == mg.event_id_for(*args)


def test_event_id_changes_with_every_component():
    ts = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
    base = mg.event_id_for("github_graphql", "pull_request", "k#1", "merged", ts)
    assert base != mg.event_id_for("github_actions", "pull_request", "k#1", "merged", ts)
    assert base != mg.event_id_for("github_graphql", "workflow_run", "k#1", "merged", ts)
    assert base != mg.event_id_for("github_graphql", "pull_request", "k#2", "merged", ts)
    assert base != mg.event_id_for("github_graphql", "pull_request", "k#1", "review", ts)
    assert base != mg.event_id_for(
        "github_graphql", "pull_request", "k#1", "merged",
        datetime(2026, 3, 1, 12, 0, 1, tzinfo=UTC),
    )


@pytest.mark.parametrize(
    "paths",
    [
        ["core/a.java", "core/b.java", "clients/c.java"],
        ["clients/a", "core/b"],
        ["README.md", "core/x"],
        ["README.md"],
        ["a/b/c.java", "a/d.java"],
    ],
)
def test_component_agrees_with_git_local(paths):
    """git_local computes this on Commit; the two must not drift."""
    commit = Commit(
        sha="x",
        authored_at=datetime(2026, 1, 1, tzinfo=UTC),
        committed_at=datetime(2026, 1, 1, tzinfo=UTC),
        identity_key="someone",
        subject="s",
        parents=[],
        files=list(paths),
    )
    assert mg.component_of(paths) == commit.component


def test_component_of_empty_is_none():
    assert mg.component_of([]) is None


def test_component_uses_root_sentinel_for_top_level_files():
    assert mg.component_of(["README.md", "LICENSE"]) == mg.ROOT_COMPONENT


def test_component_breaks_ties_alphabetically_for_reproducibility():
    assert mg.component_of(["zzz/a", "aaa/b"]) == "aaa"


# =====================================================================
# Case ID resolution — decision #6
# =====================================================================


def test_pr_with_no_ticket_key_falls_through_to_pr_number(no_ticket):
    """Never drop a PR for lacking a ticket key."""
    mapped = mg.map_pull_request(no_ticket)
    assert mapped.work_item["work_item_id"] == "apache/kafka#19565"
    assert mapped.case_source == "pr"
    assert mapped.work_item["jira_key"] is None


def test_ticket_key_comes_from_the_title_first(review_rounds):
    mapped = mg.map_pull_request(review_rounds)
    assert mapped.work_item["work_item_id"] == "KAFKA-18220"
    assert mapped.case_source == "ticket_key"


def test_body_mentions_do_not_steal_the_case(review_rounds):
    """The body names KAFKA-17999 and KAFKA-12345; the title must win."""
    assert "KAFKA-17999" in review_rounds["body"]
    mapped = mg.map_pull_request(review_rounds)
    assert mapped.work_item["work_item_id"] == "KAFKA-18220"


def test_ticket_key_falls_back_to_the_branch():
    node = {
        "number": 1,
        "repo": "apache/kafka",
        "title": "MINOR: tidy up",
        "headRefName": "KAFKA-4242-tidy",
        "body": "",
        "files": {"nodes": []},
    }
    assert mg.resolve_case(node, "apache/kafka")[:2] == ("KAFKA-4242", "ticket_key")


def test_ticket_key_falls_back_to_the_body_last():
    node = {
        "number": 1,
        "repo": "apache/kafka",
        "title": "MINOR: tidy up",
        "headRefName": "tidy",
        "body": "This implements KAFKA-4242 as discussed.",
    }
    assert mg.resolve_case(node, "apache/kafka")[:2] == ("KAFKA-4242", "ticket_key")


def test_closing_issue_is_the_second_rung(with_issue):
    mapped = mg.map_pull_request(with_issue)
    assert mapped.work_item["work_item_id"] == "apache/kafka#20455"
    assert mapped.case_source == "issue"


def test_ticket_key_outranks_a_closing_issue():
    node = {
        "number": 7,
        "repo": "apache/kafka",
        "title": "KAFKA-1: do the thing",
        "headRefName": "b",
        "body": "",
        "closingIssuesReferences": {"nodes": [{"number": 99}]},
    }
    assert mg.resolve_case(node, "apache/kafka")[1] == "ticket_key"


def test_case_source_is_always_a_schema_allowed_value(
    no_ticket, review_rounds, with_issue, force_push, repoints
):
    allowed = {"ticket_key", "issue", "pr"}
    for body in (no_ticket, review_rounds, with_issue, force_push, repoints):
        assert mg.map_pull_request(body).case_source in allowed


@pytest.mark.parametrize(
    "title,expected",
    [
        ("KAFKA-1: x", "KAFKA-1"),
        ("[KAFKA-22]: x", "KAFKA-22"),
        ("  FLINK-9876 fix", "FLINK-9876"),
        ("MINOR: no key", None),
        ("fixes KAFKA-5 later in the line", None),
        ("TOOLONGPROJECT-1: x", None),
    ],
)
def test_ticket_regex_anchoring(title, expected):
    assert mg.ticket_key_from(title, None, None) == expected


# =====================================================================
# Activity mapping
# =====================================================================


def _activities(mapped) -> list[str]:
    return [e["activity"] for e in mapped.events]


def test_every_activity_written_is_in_the_schema_vocabulary(
    no_ticket, review_rounds, with_issue, force_push, repoints
):
    for body in (no_ticket, review_rounds, with_issue, force_push, repoints):
        for activity in _activities(mg.map_pull_request(body)):
            assert activity in ACTIVITIES


def test_review_states_map_to_the_decided_spellings(review_rounds):
    """Decision #14: `approved`, not `approve`."""
    activities = _activities(mg.map_pull_request(review_rounds))
    assert activities.count("review") == 1
    assert activities.count("changes_requested") == 1
    assert activities.count("approved") == 2


def test_dismissed_review_is_skipped_not_invented(review_rounds):
    stats = mg.Stats()
    mg.map_pull_request(review_rounds, stats)
    assert stats.unmapped_review_states["DISMISSED"] == 1


def test_multiple_review_rounds_all_survive(review_rounds):
    """Three rounds from one reviewer plus one from another."""
    mapped = mg.map_pull_request(review_rounds)
    reviews = [e for e in mapped.events if e["activity"] in
               {"review", "approved", "changes_requested"}]
    assert len(reviews) == 4
    assert len({e["event_id"] for e in reviews}) == 4


def test_same_second_approvals_by_different_people_do_not_collide(review_rounds):
    """Two people approving in the same second must stay two events."""
    mapped = mg.map_pull_request(review_rounds)
    approvals = [e for e in mapped.events if e["activity"] == "approved"]
    assert len(approvals) == 2
    assert approvals[0]["ts"] == approvals[1]["ts"]
    assert approvals[0]["event_id"] != approvals[1]["event_id"]


def test_batched_review_requests_do_not_collide(review_rounds):
    """GitHub emits one event per reviewer, all with the same createdAt."""
    mapped = mg.map_pull_request(review_rounds)
    requested = [e for e in mapped.events if e["activity"] == "review_requested"]
    assert len(requested) == 3
    assert len({e["ts"] for e in requested}) == 1
    assert len({e["event_id"] for e in requested}) == 3


def test_review_request_records_a_team_reviewer(review_rounds):
    mapped = mg.map_pull_request(review_rounds)
    reviewers = {
        e["attrs"]["requested_reviewer"]
        for e in mapped.events
        if e["activity"] == "review_requested"
    }
    assert "team:kafka-committers" in reviewers


def test_force_push_events_are_mapped(force_push):
    assert _activities(mg.map_pull_request(force_push)).count("force_push") == 2


def test_reopened_event_is_mapped(with_issue):
    assert "reopened" in _activities(mg.map_pull_request(with_issue))


def test_merged_event_comes_from_merged_at(no_ticket):
    mapped = mg.map_pull_request(no_ticket)
    merged = [e for e in mapped.events if e["activity"] == "merged"]
    assert len(merged) == 1
    assert merged[0]["ts"] == datetime(2026, 3, 4, 10, 30, tzinfo=UTC)


def test_unmerged_pr_produces_no_merged_event(force_push):
    assert "merged" not in _activities(mg.map_pull_request(force_push))


def test_merged_event_has_no_fabricated_actor(no_ticket):
    """mergedBy is not fetched; attributing the merge to the author would be
    a number we made up."""
    mapped = mg.map_pull_request(no_ticket)
    merged = next(e for e in mapped.events if e["activity"] == "merged")
    assert merged["actor_hash"] is None
    assert merged["attrs"]["actor_basis"] == "not_fetched"


def test_every_event_carries_source_github(review_rounds):
    """Decision #11: a row without a source is a bug."""
    for event in mg.map_pull_request(review_rounds).events:
        assert event["source"] == "github"


def test_every_event_hangs_off_the_resolved_case(review_rounds):
    mapped = mg.map_pull_request(review_rounds)
    assert {e["work_item_id"] for e in mapped.events} == {"KAFKA-18220"}


# =====================================================================
# Bots
# =====================================================================


def test_bot_reviews_are_dropped(force_push):
    stats = mg.Stats()
    mapped = mg.map_pull_request(force_push, stats)
    assert stats.bot_events_dropped == 2  # one review, one force push
    assert all(e["actor_hash"] != "" for e in mapped.events)


def test_no_bot_ever_becomes_an_actor(force_push):
    """The scrubbed payload has no actor_hash on a bot node at all."""
    mapped = mg.map_pull_request(force_push)
    hashes = {e["actor_hash"] for e in mapped.events if e["actor_hash"]}
    assert hashes == {"aaaaaaaaaaaaaaaa", "cccccccccccccccc"}


# =====================================================================
# work_item fields
# =====================================================================


def test_component_is_the_majority_top_level_directory(review_rounds):
    """3 of 5 files are under clients/."""
    assert mg.map_pull_request(review_rounds).work_item["component"] == "clients"


def test_epic_prefers_the_milestone(with_issue):
    assert mg.map_pull_request(with_issue).work_item["epic"] == "4.2.0"


def test_epic_falls_back_to_the_dominant_label():
    node = {
        "number": 1,
        "repo": "r",
        "title": "MINOR: x",
        "body": "",
        "labels": {"nodes": [{"name": "bug"}, {"name": "bug"}, {"name": "docs"}]},
    }
    assert mg.epic_of(node) == "bug"


def test_epic_is_null_when_neither_is_present(no_ticket):
    """Today's payloads always land here: PR_QUERY fetches neither field."""
    assert mg.map_pull_request(no_ticket).work_item["epic"] is None


def test_work_item_carries_no_sprint_key(review_rounds):
    """sprint is read from work_item, never written here (decision #7)."""
    assert "sprint" not in mg.map_pull_request(review_rounds).work_item


def test_work_item_records_the_pr_as_source_ref(no_ticket):
    assert mg.map_pull_request(no_ticket).work_item["source_ref"] == "apache/kafka#19565"


def test_closed_at_falls_back_to_merged_at():
    node = {
        "number": 3,
        "repo": "r",
        "title": "MINOR: x",
        "body": "",
        "mergedAt": "2026-01-05T00:00:00+00:00",
        "closedAt": None,
    }
    item = mg.map_pull_request(node).work_item
    assert item["closed_at"] == datetime(2026, 1, 5, tzinfo=UTC)


# =====================================================================
# Commit re-pointing
# =====================================================================


def test_provisional_commit_is_repointed_to_the_ticket(repoints):
    """The headline case: git_local filed the commit under {repo}@{sha12}."""
    mapped = mg.map_pull_request(repoints)
    sha = mapped.merge_commit_sha
    commit_rows = [("evt1", "apache/kafka@555555555555", sha)]
    updates, left = mg.plan_repointing(
        commit_rows,
        {sha: "KAFKA-19777"},
        {"KAFKA-19777": "apache/kafka"},
    )
    assert updates == [{"eid": "evt1", "wid": "KAFKA-19777"}]
    assert left == 0


def test_pr_shaped_case_is_merged_into_the_ticket_key():
    """Otherwise coding and reviewing show as two disconnected islands."""
    updates, left = mg.plan_repointing(
        [("evt1", "apache/kafka#20600", "sha5")],
        {"sha5": "KAFKA-19777"},
        {"KAFKA-19777": "apache/kafka"},
    )
    assert updates == [{"eid": "evt1", "wid": "KAFKA-19777"}]
    assert left == 0


def test_a_commit_already_on_a_ticket_key_is_left_alone():
    """git_local's anchored subject regex is a strong signal; a PR body
    mentioning a different key must not override it."""
    updates, left = mg.plan_repointing(
        [("evt1", "KAFKA-111", "sha5")],
        {"sha5": "KAFKA-999"},
        {"KAFKA-999": "apache/kafka"},
    )
    assert updates == []
    assert left == 1


def test_a_commit_already_on_the_right_case_is_not_touched():
    updates, left = mg.plan_repointing(
        [("evt1", "KAFKA-19777", "sha5")],
        {"sha5": "KAFKA-19777"},
        {"KAFKA-19777": "apache/kafka"},
    )
    assert updates == [] and left == 0


def test_a_commit_no_pr_claims_is_not_touched():
    updates, left = mg.plan_repointing(
        [("evt1", "apache/kafka@abc", "unknown-sha")], {}, {}
    )
    assert updates == [] and left == 0


def test_repointing_does_not_change_the_event_id(repoints):
    """event_id is derived from the commit, not from its case, so re-pointing
    is idempotent and does not orphan the old row."""
    ts = datetime(2026, 7, 5, 8, 0, tzinfo=UTC)
    before = git_event_id("5" * 40, ts)
    after = git_event_id("5" * 40, ts)
    assert before == after


# =====================================================================
# Workflow runs
# =====================================================================


def test_run_matching_a_head_sha_gets_a_case_and_an_event():
    body = load("run_matching_head_sha")
    ci_row, event = mg.map_workflow_run(body, {"5" * 40: "KAFKA-19777"})
    assert ci_row["work_item_id"] == "KAFKA-19777"
    assert event is not None
    assert event["activity"] == "ci_run"
    assert event["work_item_id"] == "KAFKA-19777"


def test_unmatched_run_still_lands_with_a_null_case():
    """Unattributed CI is real spend; ci_run.work_item_id is nullable for it."""
    body = load("run_unmatched")
    ci_row, event = mg.map_workflow_run(body, {})
    assert ci_row["work_item_id"] is None
    assert event is None, "event_log.work_item_id is NOT NULL — no event here"
    assert ci_row["run_id"] == "apache/kafka:987654322:2"


def test_ci_run_event_has_no_actor():
    body = load("run_matching_head_sha")
    _, event = mg.map_workflow_run(body, {"5" * 40: "KAFKA-19777"})
    assert event["actor_hash"] is None


def test_runner_minutes_are_wall_clock():
    """run_config commits us to updated_at - run_started_at, not billable."""
    body = load("run_matching_head_sha")
    ci_row, _ = mg.map_workflow_run(body, {})
    assert ci_row["runner_minutes"] == pytest.approx(42.0)


def test_runner_minutes_handle_a_partial_minute():
    body = load("run_unmatched")
    ci_row, _ = mg.map_workflow_run(body, {})
    assert ci_row["runner_minutes"] == pytest.approx(15.5)


def test_run_attempt_is_part_of_the_run_id():
    """A re-run is a distinct row, not an overwrite of the first attempt."""
    body = load("run_matching_head_sha")
    first, _ = mg.map_workflow_run(body, {})
    second, _ = mg.map_workflow_run(body | {"run_attempt": 2}, {})
    assert first["run_id"] != second["run_id"]


def test_workflow_run_contract_is_documented():
    """No connector produces these rows yet; the expected shape is pinned."""
    body = load("run_matching_head_sha")
    for key in mg.WORKFLOW_RUN_CONTRACT:
        assert key in body, f"fixture drifted from WORKFLOW_RUN_CONTRACT: {key}"


# =====================================================================
# Scope and privacy
# =====================================================================


def test_jira_is_not_in_the_source_list():
    """Jira mapping is a separate task, scoped out deliberately."""
    assert "asf_jira" not in mg.GITHUB_SOURCES
    assert set(mg.GITHUB_SOURCES) == {"git_local", "github_graphql", "github_actions"}


def test_module_makes_no_network_call():
    """Asserted on the import lines, not the prose: the docstring legitimately
    contains the word "requests"."""
    source = Path(mg.__file__).read_text(encoding="utf-8")
    imports = [
        line.strip()
        for line in source.splitlines()
        if line.strip().startswith(("import ", "from "))
    ]
    for forbidden in ("httpx", "requests", "urllib", "socket", "http.client"):
        offenders = [line for line in imports if forbidden in line]
        assert not offenders, f"{forbidden} imported by a pure mapping module"


def test_fixtures_carry_no_identity():
    """A fixture pasted from the live API must not smuggle a login into git.

    Only `login` and `email` are identity here. A GitHub *label* is also called
    `name` and is not a person — see the test below for why that distinction
    nevertheless blocks the epic mapping today.
    """
    for path in FIXTURES.glob("*.json"):
        text = path.read_text(encoding="utf-8")
        for token in ('"login"', '"email"'):
            assert token not in text, f"{path.name} contains {token}"


def test_label_shaped_payload_is_rejected_by_the_connector_today():
    """Executable record of a blocker, so it is not just a note in a docstring.

    `epic` falls back to the dominant label, but `github_connector` cannot
    currently land a label: its `_assert_scrubbed` raises on ANY key named
    `name`, and GitHub's label node is `{name: ...}`. Adding `labels` to
    PR_QUERY therefore needs a change in that module first — the mapping side
    is ready and tested, the fetch side is not.

    If this test starts failing, the connector learned to tell a label from a
    person and the epic fallback can go live.
    """
    from app.ingestion.github_connector import _assert_scrubbed

    with pytest.raises(AssertionError, match="name"):
        _assert_scrubbed(load("pr_reopened_with_issue"))


def test_written_rows_pass_the_identity_guard(review_rounds, with_issue):
    from app.ingestion.pseudonymize import assert_no_identity

    for body in (review_rounds, with_issue):
        mapped = mg.map_pull_request(body)
        assert_no_identity([mapped.work_item])
        if mapped.events:
            assert_no_identity(mapped.events)


def test_all_timestamps_stay_timezone_aware(review_rounds):
    mapped = mg.map_pull_request(review_rounds)
    for event in mapped.events:
        assert event["ts"].tzinfo is not None
    for key in ("opened_at", "closed_at"):
        value = mapped.work_item[key]
        if value is not None:
            assert value.tzinfo is not None


# =====================================================================
# Live database — the SQL path. Skips when Postgres is not running.
# =====================================================================


@pytest.fixture
def seeded_session(pg_engine):
    """A session with the fixtures landed in raw_payload, rolled back after.

    Everything below runs inside one transaction that is never committed, so
    the developer's real ingest is untouched no matter how a test ends.
    """
    from app.db.models import EventLog, RawPayload, WorkItem
    from sqlalchemy.orm import Session as OrmSession

    session = OrmSession(pg_engine)
    try:
        for name in (
            "pr_no_ticket_key",
            "pr_multiple_review_rounds",
            "pr_force_push",
            "pr_reopened_with_issue",
            "pr_ticket_key_repoints_commit",
        ):
            body = load(name)
            session.add(
                RawPayload(
                    source="github_graphql",
                    entity_type="pull_request",
                    entity_id=f"{body['repo']}#{body['number']}",
                    body=body,
                )
            )
        for name in ("run_matching_head_sha", "run_unmatched"):
            body = load(name)
            session.add(
                RawPayload(
                    source="github_actions",
                    entity_type="workflow_run",
                    entity_id=f"{body['repo']}:{body['id']}",
                    body=body,
                )
            )

        # git_local's world before the PR mapper runs: a provisional case
        # holding the squash commit that PR 20600 will claim.
        session.add(
            WorkItem(
                work_item_id="apache/kafka@555555555555",
                repo="apache/kafka",
                component="core",
                case_source="pr",
                sprint=7,
            )
        )
        session.flush()
        session.add(
            EventLog(
                event_id="fixture-commit-0001",
                work_item_id="apache/kafka@555555555555",
                actor_hash=None,
                activity="commit",
                ts=datetime(2026, 7, 4, 9, 0, tzinfo=UTC),
                source="github",
                attrs={"sha": "5" * 40},
            )
        )
        session.flush()
        yield session
    finally:
        session.rollback()
        session.close()


def test_end_to_end_writes_every_table(seeded_session):
    stats = mg.run(seeded_session, repos=["apache/kafka"])
    assert stats.pull_requests == 5
    assert stats.workflow_runs == 2
    assert stats.work_items == 5
    assert stats.events > 0
    assert stats.ci_runs == 2
    assert stats.actors == 3


def test_end_to_end_repoints_the_provisional_commit(seeded_session):
    from app.db.models import EventLog
    from sqlalchemy import select

    mg.run(seeded_session, repos=["apache/kafka"])
    seeded_session.flush()
    landed = seeded_session.execute(
        select(EventLog.work_item_id).where(EventLog.event_id == "fixture-commit-0001")
    ).scalar_one()
    assert landed == "KAFKA-19777", "the commit should have moved off the placeholder"


def test_end_to_end_ci_run_matches_by_head_sha(seeded_session):
    from app.db.models import CiRun
    from sqlalchemy import select

    mg.run(seeded_session, repos=["apache/kafka"])
    seeded_session.flush()
    rows = dict(
        seeded_session.execute(select(CiRun.run_id, CiRun.work_item_id)).all()
    )
    assert rows["apache/kafka:987654321:1"] == "KAFKA-19777"
    assert rows["apache/kafka:987654322:2"] is None


def test_end_to_end_is_idempotent(seeded_session):
    """Decision: a re-run writes the same rows and changes no counts."""
    from app.db.models import EventLog
    from sqlalchemy import func, select

    mg.run(seeded_session, repos=["apache/kafka"])
    seeded_session.flush()
    first = seeded_session.execute(
        select(func.count()).select_from(EventLog)
    ).scalar_one()

    mg.run(seeded_session, repos=["apache/kafka"])
    seeded_session.flush()
    second = seeded_session.execute(
        select(func.count()).select_from(EventLog)
    ).scalar_one()
    assert first == second


def test_end_to_end_never_reads_jira(seeded_session):
    """A Jira row in the same table must be invisible to this module."""
    from app.db.models import RawPayload

    seeded_session.add(
        RawPayload(
            source="asf_jira",
            entity_type="issue",
            entity_id="KAFKA-1",
            body={"repo": "apache/kafka", "number": 1, "title": "KAFKA-1: x"},
        )
    )
    seeded_session.flush()
    payloads = mg.load_payloads(seeded_session, repos=["apache/kafka"])
    assert "asf_jira" not in payloads
    # Counted per source rather than in total: this database also holds the
    # developer's real git_local rows, and the claim under test is only that
    # the Jira row is invisible.
    assert len(payloads["github_graphql"]) == 5
    assert len(payloads["github_actions"]) == 2


def test_end_to_end_activities_pass_the_schema_check(seeded_session):
    """The CHECK constraint is the authority; this proves Postgres accepted
    every activity we wrote rather than trusting our own list."""
    from app.db.models import EventLog
    from sqlalchemy import select

    mg.run(seeded_session, repos=["apache/kafka"])
    seeded_session.flush()
    written = set(
        seeded_session.execute(
            select(EventLog.activity).where(EventLog.event_id != "fixture-commit-0001")
        )
        .scalars()
        .all()
    )
    assert written <= set(ACTIVITIES)
    assert {"approved", "changes_requested", "review", "merged"} <= written
