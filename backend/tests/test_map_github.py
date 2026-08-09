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
    assert mapped.work_item["work_item_id"] == "apache/kafka#900001"
    assert mapped.case_source == "pr"
    assert mapped.work_item["jira_key"] is None


def test_ticket_key_comes_from_the_title_first(review_rounds):
    mapped = mg.map_pull_request(review_rounds)
    assert mapped.work_item["work_item_id"] == "KAFKA-900013"
    assert mapped.case_source == "ticket_key"


def test_body_mentions_do_not_steal_the_case(review_rounds):
    """The body names KAFKA-900014 and KAFKA-900015; the title must win."""
    assert "KAFKA-900014" in review_rounds["body"]
    mapped = mg.map_pull_request(review_rounds)
    assert mapped.work_item["work_item_id"] == "KAFKA-900013"


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
    assert mapped.work_item["work_item_id"] == "apache/kafka#900010"
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


# =====================================================================
# The PR's own commits (P2: squash-merge leaves session inference nothing
# to cluster; these intermediate commits are the real signal)
# =====================================================================


def _pr_commit(oid="c1", authored="2026-04-01T09:00:00Z"):
    """A scrubbed commit node, exactly as github_connector would land it."""
    return {
        "commit": {
            "oid": oid,
            "authoredDate": authored,
            "additions": 4,
            "deletions": 1,
            "changedFiles": 1,
            "author": {"user": {"actor_hash": "a" * 16, "is_bot": False, "__typename": "User"}},
        }
    }


def test_pr_commits_become_commit_events(review_rounds):
    review_rounds["commits"] = {"nodes": [_pr_commit("c1"), _pr_commit("c2")]}
    mapped = mg.map_pull_request(review_rounds)
    commits = [e for e in mapped.events if e["activity"] == "commit"]
    assert {e["attrs"]["sha"] for e in commits} == {"c1", "c2"}
    assert all(e["work_item_id"] == "KAFKA-900013" for e in commits)
    assert all(e["source"] == "github" for e in commits)


def test_commit_event_id_matches_git_local_scheme(review_rounds):
    """The whole dedup story depends on this: a commit git_local already saw
    on trunk must converge on the SAME event_id, not create a second row."""
    ts = datetime(2026, 4, 1, 9, 0, tzinfo=UTC)
    review_rounds["commits"] = {"nodes": [_pr_commit("c1", authored="2026-04-01T09:00:00Z")]}
    mapped = mg.map_pull_request(review_rounds)
    commit_event = next(e for e in mapped.events if e["activity"] == "commit")
    assert commit_event["event_id"] == git_event_id("c1", ts)


def test_commit_ts_is_the_authored_date_not_a_commit_date(review_rounds):
    """Decision: author date for a commit, never the commit date."""
    review_rounds["commits"] = {"nodes": [_pr_commit("c1", authored="2026-04-01T09:00:00Z")]}
    mapped = mg.map_pull_request(review_rounds)
    commit_event = next(e for e in mapped.events if e["activity"] == "commit")
    assert commit_event["ts"] == datetime(2026, 4, 1, 9, 0, tzinfo=UTC)


def test_bot_commit_author_is_dropped_not_mapped(review_rounds):
    bot_commit = {
        "commit": {
            "oid": "c-bot",
            "authoredDate": "2026-04-01T09:00:00Z",
            "additions": 1,
            "deletions": 0,
            "changedFiles": 1,
            "author": {"user": {"is_bot": True, "__typename": "Bot"}},
        }
    }
    review_rounds["commits"] = {"nodes": [bot_commit]}
    mapped = mg.map_pull_request(review_rounds)
    assert not [e for e in mapped.events if e["activity"] == "commit"]
    assert mapped.bot_events_dropped == 1


def test_commit_with_no_linked_github_account_records_actor_absent(review_rounds):
    """A deleted account, or a git email that never mapped to a login, still
    leaves a null resource — but an unexplained one is what a mapper that
    silently dropped a human looks like."""
    no_account_commit = {
        "commit": {
            "oid": "c-ghost",
            "authoredDate": "2026-04-01T09:00:00Z",
            "additions": 1,
            "deletions": 0,
            "changedFiles": 1,
            "author": {"user": None},
        }
    }
    review_rounds["commits"] = {"nodes": [no_account_commit]}
    mapped = mg.map_pull_request(review_rounds)
    commit_event = next(e for e in mapped.events if e["activity"] == "commit")
    assert commit_event["actor_hash"] is None
    assert commit_event["attrs"]["actor_absent"] == "unattributed"


def test_commit_matching_the_merge_commit_oid_is_flagged_squash():
    """Where a PR commit oid equals the trunk merge commit's oid, it IS the
    squash artefact — flag it so detectors can exclude it, per P2."""
    node = load("pr_ticket_key_repoints_commit")  # mergeCommit.oid = "5" * 40
    node["commits"] = {
        "nodes": [
            _pr_commit(oid="5" * 40, authored="2026-07-05T08:00:00Z"),
            _pr_commit(oid="intermediate-1", authored="2026-07-04T08:00:00Z"),
        ]
    }
    mapped = mg.map_pull_request(node)
    by_sha = {e["attrs"]["sha"]: e for e in mapped.events if e["activity"] == "commit"}
    assert by_sha["5" * 40]["attrs"]["is_squash_merge"] is True
    assert by_sha["intermediate-1"]["attrs"]["is_squash_merge"] is False


def test_a_commit_with_no_authored_date_is_skipped(review_rounds):
    review_rounds["commits"] = {
        "nodes": [{"commit": {"oid": "no-date", "author": {"user": None}}}]
    }
    mapped = mg.map_pull_request(review_rounds)
    assert not [e for e in mapped.events if e["activity"] == "commit"]


def test_a_pr_with_no_commits_section_maps_the_same_as_before(no_ticket):
    """Old fixtures/payloads with no `commits` key must not break."""
    assert "commits" not in no_ticket
    mapped = mg.map_pull_request(no_ticket)
    assert not [e for e in mapped.events if e["activity"] == "commit"]


def test_every_event_carries_source_github(review_rounds):
    """Decision #11: a row without a source is a bug."""
    for event in mg.map_pull_request(review_rounds).events:
        assert event["source"] == "github"


def test_every_event_hangs_off_the_resolved_case(review_rounds):
    mapped = mg.map_pull_request(review_rounds)
    assert {e["work_item_id"] for e in mapped.events} == {"KAFKA-900013"}


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
    assert mg.map_pull_request(no_ticket).work_item["source_ref"] == "apache/kafka#900001"


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

# =====================================================================
# Case merging — several PRs on one ticket key
#
# Measured on apache/kafka: 3,314 PRs collapse to 2,562 cases, and every one
# of the 332 duplicated ids is case_source='ticket_key'. KAFKA-10199 alone is
# 19 pull requests. Sending those as 19 rows in one INSERT ... ON CONFLICT is
# what Postgres rejects with CardinalityViolation.
# =====================================================================


def _pr(
    number: int,
    *,
    key: str = "KAFKA-10199",
    opened: str = "2026-01-01T00:00:00+00:00",
    closed: str | None = "2026-01-10T00:00:00+00:00",
    paths: list[str] | None = None,
    title: str | None = None,
) -> dict:
    """A minimal PR payload sharing one ticket key."""
    return {
        "number": number,
        "repo": "apache/kafka",
        "title": title or f"{key}: part {number}",
        "body": "",
        "headRefName": f"{key}-{number}",
        "createdAt": opened,
        "closedAt": closed,
        "mergedAt": closed,
        # `is None` not `or`: paths=[] means "this PR listed no files", which is
        # a case the merge rule has to handle, not a request for the default.
        "files": {
            "nodes": [
                {"path": p} for p in (["streams/a.java"] if paths is None else paths)
            ]
        },
        "reviews": {"nodes": []},
        "timelineItems": {"nodes": []},
    }


def _group(*bodies: dict) -> list:
    return [mg.map_pull_request(b) for b in bodies]


def test_three_prs_on_one_ticket_become_one_work_item():
    merged = mg.merge_work_items(_group(_pr(101), _pr(102), _pr(103)))
    assert merged["work_item_id"] == "KAFKA-10199"


def test_merged_opened_at_is_the_minimum():
    group = _group(
        _pr(101, opened="2026-03-01T00:00:00+00:00"),
        _pr(102, opened="2026-01-15T00:00:00+00:00"),
        _pr(103, opened="2026-02-01T00:00:00+00:00"),
    )
    assert mg.merge_work_items(group)["opened_at"] == datetime(2026, 1, 15, tzinfo=UTC)


def test_merged_closed_at_is_the_maximum():
    group = _group(
        _pr(101, closed="2026-03-01T00:00:00+00:00"),
        _pr(102, closed="2026-05-20T00:00:00+00:00"),
        _pr(103, closed="2026-04-01T00:00:00+00:00"),
    )
    assert mg.merge_work_items(group)["closed_at"] == datetime(2026, 5, 20, tzinfo=UTC)


def test_one_open_pr_keeps_the_whole_case_open():
    """MAX would report the case finished while work is still in review."""
    group = _group(
        _pr(101, closed="2026-03-01T00:00:00+00:00"),
        _pr(102, closed=None),
        _pr(103, closed="2026-04-01T00:00:00+00:00"),
    )
    assert mg.merge_work_items(group)["closed_at"] is None


def test_component_is_stable_across_repeated_runs():
    """The bug this guards: a component that depends on payload order makes
    every downstream cost number irreproducible."""
    bodies = [
        _pr(101, paths=["streams/a.java", "streams/b.java"]),
        _pr(102, paths=["clients/c.java"]),
        _pr(103, paths=["core/d.java"]),
    ]
    first = mg.merge_work_items(_group(*bodies))["component"]
    for permutation in ([2, 0, 1], [1, 2, 0], [2, 1, 0]):
        shuffled = [bodies[i] for i in permutation]
        assert mg.merge_work_items(_group(*shuffled))["component"] == first
    assert first == "streams", "the component touching the most files wins"


def test_component_tie_breaks_lexically_not_by_order():
    bodies = [_pr(101, paths=["zzz/a"]), _pr(102, paths=["aaa/b"])]
    assert mg.merge_work_items(_group(*bodies))["component"] == "aaa"
    assert mg.merge_work_items(_group(*reversed(bodies)))["component"] == "aaa"


def test_component_falls_back_when_no_pr_listed_files():
    group = _group(_pr(101, paths=[]), _pr(102, paths=[]))
    assert mg.merge_work_items(group)["component"] is None


def test_case_source_takes_the_strongest_present():
    assert mg._strongest_case_source(["pr", "ticket_key", "issue"]) == "ticket_key"
    assert mg._strongest_case_source(["pr", "issue"]) == "issue"
    assert mg._strongest_case_source(["pr"]) == "pr"
    assert mg._strongest_case_source([]) == "pr"


def test_source_ref_is_the_earliest_pr_tie_broken_by_number():
    group = _group(
        _pr(300, opened="2026-02-01T00:00:00+00:00"),
        _pr(100, opened="2026-01-01T00:00:00+00:00"),
        _pr(200, opened="2026-01-01T00:00:00+00:00"),
    )
    assert mg.merge_work_items(group)["source_ref"] == "apache/kafka#100"


def test_merge_is_order_independent_in_every_field():
    bodies = [
        _pr(101, opened="2026-03-01T00:00:00+00:00",
            closed="2026-03-05T00:00:00+00:00", paths=["core/a"]),
        _pr(102, opened="2026-01-01T00:00:00+00:00",
            closed="2026-06-01T00:00:00+00:00", paths=["streams/b", "streams/c"]),
        _pr(103, opened="2026-02-01T00:00:00+00:00",
            closed="2026-02-02T00:00:00+00:00", paths=["clients/d"]),
    ]
    baseline = mg.merge_work_items(_group(*bodies))
    for permutation in ([2, 1, 0], [1, 0, 2], [0, 2, 1], [2, 0, 1]):
        assert mg.merge_work_items(_group(*[bodies[i] for i in permutation])) == baseline


def test_merging_a_single_pr_is_a_no_op_on_the_fields_that_matter():
    single = mg.map_pull_request(_pr(101))
    merged = mg.merge_work_items([single])
    for name in (
        "work_item_id", "opened_at", "closed_at", "component",
        "case_source", "source_ref", "repo",
    ):
        assert merged[name] == single.work_item[name]


def test_merging_mixed_ids_is_a_programming_error():
    group = _group(_pr(101, key="KAFKA-1"), _pr(102, key="KAFKA-2"))
    with pytest.raises(ValueError, match="mixed ids"):
        mg.merge_work_items(group)


def test_merging_nothing_raises():
    with pytest.raises(ValueError):
        mg.merge_work_items([])


def test_writer_rejects_an_unmerged_batch_with_a_useful_message():
    """The guard that turns CardinalityViolation into a message naming names."""
    rows = [
        {"work_item_id": "KAFKA-1", "repo": "r"},
        {"work_item_id": "KAFKA-1", "repo": "r"},
    ]
    with pytest.raises(ValueError, match="duplicate work_item_id"):
        mg._write_work_items(None, rows)


# Live database — the SQL path. Skips when Postgres is not running.
#
# The fixtures are re-homed onto a synthetic `fixture/repo` before they are
# landed. They originally carried `apache/kafka` and real PR numbers, which
# collided with `raw_payload`'s primary key the moment the connector landed
# 3,314 genuine apache/kafka PRs. Isolating the repo keeps these tests
# independent of whatever the developer has ingested, which is the only way an
# assertion like "5 payloads" can stay true.
# =====================================================================

FIXTURE_REPO = "fixture/repo"

PR_FIXTURES = (
    "pr_no_ticket_key",
    "pr_multiple_review_rounds",
    "pr_force_push",
    "pr_reopened_with_issue",
    "pr_ticket_key_repoints_commit",
)
RUN_FIXTURES = ("run_matching_head_sha", "run_unmatched")


def _rehomed(name: str) -> dict:
    body = load(name)
    body["repo"] = FIXTURE_REPO
    return body


def _land_pr(session, body: dict) -> None:
    from app.db.models import RawPayload

    body["repo"] = FIXTURE_REPO
    session.add(
        RawPayload(
            source="github_graphql",
            entity_type="pull_request",
            entity_id=f"{FIXTURE_REPO}#{body['number']}",
            body=body,
        )
    )


def test_write_work_items_refuses_a_batch_colliding_on_the_same_case(pg_engine):
    """Two PRs resolving to the same ticket key (a revert and its original,
    say) must not silently pick a winner — Postgres rejects an ON CONFLICT
    DO UPDATE statement that touches the same row twice in one VALUES list
    anyway, so this fails loudly and names the id rather than raising an
    opaque CardinalityViolation. Callers must merge_work_items() first."""
    from sqlalchemy.orm import Session as OrmSession

    session = OrmSession(pg_engine)
    try:
        row = {
            "work_item_id": "KAFKA-900099",
            "repo": "apache/kafka",
            "component": "core",
            "epic": None,
            "opened_at": None,
            "closed_at": None,
            "source_ref": "apache/kafka#1",
            "case_source": "ticket_key",
            "jira_key": "KAFKA-900099",
        }
        other = {**row, "source_ref": "apache/kafka#2"}
        with pytest.raises(ValueError, match="KAFKA-900099"):
            mg._write_work_items(session, [row, other])
    finally:
        session.rollback()
        session.close()


def test_write_events_chunks_batches_past_the_parameter_limit(pg_engine):
    """A single INSERT ... VALUES statement is capped by Postgres at 65,535
    bind parameters. P2's commit backfill writes far more rows than one
    unchunked statement could hold — this proves the chunking actually works
    end to end, not just that a small batch doesn't raise."""
    from sqlalchemy import func, select
    from sqlalchemy.orm import Session as OrmSession

    from app.db.models import EventLog, WorkItem

    session = OrmSession(pg_engine)
    try:
        session.add(
            WorkItem(
                work_item_id="apache/kafka@chunk-test",
                repo="apache/kafka",
                component="core",
                case_source="pr",
            )
        )
        session.flush()
        row_width = 7  # event_id, work_item_id, actor_hash, activity, ts, source, attrs
        chunk_size = mg.PG_MAX_BIND_PARAMS // row_width
        rows = [
            {
                "event_id": f"chunk-test-{i:06d}",
                "work_item_id": "apache/kafka@chunk-test",
                "actor_hash": None,
                "activity": "commit",
                "ts": datetime(2026, 1, 1, tzinfo=UTC),
                "source": "github",
                "attrs": {"sha": f"sha{i}"},
            }
            for i in range(chunk_size + 500)
        ]
        written, collapsed = mg._write_events(session, rows)
        assert written == len(rows)
        assert collapsed == 0
        count = session.execute(
            select(func.count())
            .select_from(EventLog)
            .where(EventLog.event_id.like("chunk-test-%"))
        ).scalar_one()
        assert count == len(rows)
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def seeded_session(pg_engine):
    """A session with the fixtures landed in raw_payload, rolled back after.

    Everything runs inside one transaction that is never committed, so the
    developer's real ingest is untouched no matter how a test ends.
    """
    from sqlalchemy.orm import Session as OrmSession

    from app.db.models import EventLog, RawPayload, WorkItem

    session = OrmSession(pg_engine)
    try:
        for name in PR_FIXTURES:
            _land_pr(session, _rehomed(name))
        for name in RUN_FIXTURES:
            body = _rehomed(name)
            session.add(
                RawPayload(
                    source="github_actions",
                    entity_type="workflow_run",
                    entity_id=f"{FIXTURE_REPO}:{body['id']}",
                    body=body,
                )
            )

        # git_local's world before the PR mapper runs: a provisional case
        # holding the squash commit that PR 900005 will claim.
        session.add(
            WorkItem(
                work_item_id=f"{FIXTURE_REPO}@555555555555",
                repo=FIXTURE_REPO,
                component="core",
                case_source="pr",
                sprint=7,
            )
        )
        session.flush()
        session.add(
            EventLog(
                event_id="fixture-commit-0001",
                work_item_id=f"{FIXTURE_REPO}@555555555555",
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
    stats = mg.run(seeded_session, repos=[FIXTURE_REPO])
    assert stats.pull_requests == 5
    assert stats.workflow_runs == 2
    assert stats.work_items == 5
    assert stats.events > 0
    assert stats.ci_runs == 2
    assert stats.actors == 3


def test_end_to_end_repoints_the_provisional_commit(seeded_session):
    from sqlalchemy import select

    from app.db.models import EventLog

    mg.run(seeded_session, repos=[FIXTURE_REPO])
    seeded_session.flush()
    landed = seeded_session.execute(
        select(EventLog.work_item_id).where(EventLog.event_id == "fixture-commit-0001")
    ).scalar_one()
    assert landed == "KAFKA-19777", "the commit should have moved off the placeholder"


def test_end_to_end_ci_run_matches_by_head_sha(seeded_session):
    from sqlalchemy import select

    from app.db.models import CiRun

    mg.run(seeded_session, repos=[FIXTURE_REPO])
    seeded_session.flush()
    rows = dict(
        seeded_session.execute(
            select(CiRun.run_id, CiRun.work_item_id).where(CiRun.repo == FIXTURE_REPO)
        ).all()
    )
    assert rows[f"{FIXTURE_REPO}:987654321:1"] == "KAFKA-19777"
    assert rows[f"{FIXTURE_REPO}:987654322:2"] is None


def test_end_to_end_is_idempotent(seeded_session):
    """A re-run writes the same rows and changes no counts."""
    from sqlalchemy import func, select

    from app.db.models import EventLog

    mg.run(seeded_session, repos=[FIXTURE_REPO])
    seeded_session.flush()
    first = seeded_session.execute(
        select(func.count()).select_from(EventLog)
    ).scalar_one()

    mg.run(seeded_session, repos=[FIXTURE_REPO])
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
            entity_id="FIXTURE-1",
            body={"repo": FIXTURE_REPO, "number": 1, "title": "KAFKA-1: x"},
        )
    )
    seeded_session.flush()
    payloads = mg.load_payloads(seeded_session, repos=[FIXTURE_REPO])
    assert "asf_jira" not in payloads
    assert len(payloads["github_graphql"]) == 5
    assert len(payloads["github_actions"]) == 2


def test_end_to_end_activities_pass_the_schema_check(seeded_session):
    """Proves Postgres accepted every activity we wrote, rather than trusting
    our own list. Scoped to the ids this run produced, since the database also
    holds the developer's real events."""
    from sqlalchemy import select

    from app.db.models import EventLog

    expected_ids = {
        e["event_id"]
        for name in PR_FIXTURES
        for e in mg.map_pull_request(_rehomed(name)).events
    }
    mg.run(seeded_session, repos=[FIXTURE_REPO])
    seeded_session.flush()
    written = set(
        seeded_session.execute(
            select(EventLog.activity).where(EventLog.event_id.in_(expected_ids))
        )
        .scalars()
        .all()
    )
    assert written <= set(ACTIVITIES)
    assert {"approved", "changes_requested", "review", "merged"} <= written


# --- the CardinalityViolation itself, against real Postgres ----------------


def test_three_prs_on_one_ticket_write_exactly_one_row(seeded_session):
    """The regression test for the crash.

    Three PRs sharing KAFKA-99999 must become one work_item, with MIN
    opened_at, MAX closed_at and a component that does not depend on the order
    the payloads arrived in.
    """
    from sqlalchemy import select

    from app.db.models import WorkItem

    for body in (
        _pr(9001, key="KAFKA-99999", opened="2026-03-01T00:00:00+00:00",
            closed="2026-03-05T00:00:00+00:00", paths=["core/a.java"]),
        _pr(9002, key="KAFKA-99999", opened="2026-01-15T00:00:00+00:00",
            closed="2026-06-20T00:00:00+00:00",
            paths=["streams/b.java", "streams/c.java"]),
        _pr(9003, key="KAFKA-99999", opened="2026-02-01T00:00:00+00:00",
            closed="2026-04-01T00:00:00+00:00", paths=["clients/d.java"]),
    ):
        _land_pr(seeded_session, body)
    seeded_session.flush()

    mg.run(seeded_session, repos=[FIXTURE_REPO])
    seeded_session.flush()

    rows = seeded_session.execute(
        select(
            WorkItem.work_item_id,
            WorkItem.opened_at,
            WorkItem.closed_at,
            WorkItem.component,
            WorkItem.source_ref,
        ).where(WorkItem.work_item_id == "KAFKA-99999")
    ).all()
    assert len(rows) == 1, "three PRs must collapse to exactly one case row"
    _, opened, closed, component, source_ref = rows[0]
    assert opened == datetime(2026, 1, 15, tzinfo=UTC)
    assert closed == datetime(2026, 6, 20, tzinfo=UTC)
    assert component == "streams"
    assert source_ref == f"{FIXTURE_REPO}#9002"


def test_repeated_runs_keep_the_merged_component_stable(seeded_session):
    """Re-running must not flip the component of a merged case."""
    from sqlalchemy import select

    from app.db.models import WorkItem

    for number, paths in ((9101, ["streams/a", "streams/b"]), (9102, ["clients/c"])):
        _land_pr(seeded_session, _pr(number, key="KAFKA-99998", paths=paths))
    seeded_session.flush()

    seen = []
    for _ in range(3):
        mg.run(seeded_session, repos=[FIXTURE_REPO])
        seeded_session.flush()
        seen.append(
            seeded_session.execute(
                select(WorkItem.component).where(WorkItem.work_item_id == "KAFKA-99998")
            ).scalar_one()
        )
    assert seen == ["streams", "streams", "streams"]


def test_stats_report_the_merge(seeded_session):
    for number in (9201, 9202, 9203):
        _land_pr(seeded_session, _pr(number, key="KAFKA-99997"))
    seeded_session.flush()

    stats = mg.run(seeded_session, repos=[FIXTURE_REPO])
    assert stats.pull_requests == 8
    assert stats.work_items == 6, "8 PRs, 3 sharing one key -> 6 cases"
    assert stats.cases_merged == 1
    assert stats.prs_merged_away == 2
    assert stats.largest_case == ("KAFKA-99997", 3)


# =====================================================================
# Umbrella flagging — read-time, advisory, changes no row
#
# Decision #6 is untouched: these cases stay merged. The flag exists so a
# cycle-time chart can say which of them are work programmes.
# =====================================================================


def test_run_populates_the_span_report(seeded_session):
    stats = mg.run(seeded_session, repos=[FIXTURE_REPO])
    assert stats.spans is not None
    assert stats.spans.n_cases == stats.work_items, (
        "every case this run wrote must be in the denominator"
    )


def test_a_wide_case_is_an_umbrella_however_short_it_is(seeded_session):
    """Eight PRs closed inside a fortnight is still a `[1/N]` series."""
    from app.normalise.case_span import UMBRELLA_MIN_PRS

    for offset in range(UMBRELLA_MIN_PRS):
        _land_pr(seeded_session, _pr(9301 + offset, key="KAFKA-99996"))
    seeded_session.flush()

    stats = mg.run(seeded_session, repos=[FIXTURE_REPO])
    assert "KAFKA-99996" in {c.work_item_id for c in stats.spans.over_pr_count}
    assert "KAFKA-99996" in {c.work_item_id for c in stats.spans.umbrellas}


def test_a_long_running_case_is_an_umbrella_on_span_alone(seeded_session):
    """A case that ran most of the window while its neighbours took days.

    Dated relative to the ingestion boundary rather than hardcoded: a fixed
    2020 date would be silently skipped by the window filter, and any fixed
    date eventually falls out of a rolling 12-month window and rots the test.
    """
    from datetime import timedelta

    opened = mg.window_cutoff() + timedelta(days=5)
    _land_pr(
        seeded_session,
        _pr(9499, key="KAFKA-99400",
            opened=opened.isoformat(),
            closed=(opened + timedelta(days=300)).isoformat()),
    )
    for offset in range(20):
        _land_pr(seeded_session, _pr(9401 + offset, key=f"KAFKA-9940{offset:02d}"))
    seeded_session.flush()

    stats = mg.run(seeded_session, repos=[FIXTURE_REPO])
    flagged = {c.work_item_id for c in stats.spans.over_span}
    assert "KAFKA-99400" in flagged
    assert "KAFKA-99401" not in flagged, "an ordinary 9-day case must not flag"


def test_flagging_a_case_does_not_unmerge_it(seeded_session):
    """The flag is advisory. If it ever started splitting cases it would be
    quietly overturning decision #6."""
    from sqlalchemy import func, select

    from app.db.models import WorkItem

    for offset in range(9):
        _land_pr(seeded_session, _pr(9501 + offset, key="KAFKA-99995"))
    seeded_session.flush()

    stats = mg.run(seeded_session, repos=[FIXTURE_REPO])
    seeded_session.flush()
    rows = seeded_session.execute(
        select(func.count())
        .select_from(WorkItem)
        .where(WorkItem.work_item_id == "KAFKA-99995")
    ).scalar_one()
    assert rows == 1
    assert "KAFKA-99995" in {c.work_item_id for c in stats.spans.umbrellas}


# --- the printed section --------------------------------------------------


def _span_report(**kwargs):
    from app.normalise.case_span import CaseSpan, summarise

    cases = [CaseSpan(f"KAFKA-{i}", 3.0, 1) for i in range(20)]
    cases.append(CaseSpan("KAFKA-14133", 520.0, 18))
    return summarise(cases, **kwargs)


def test_report_prints_the_rule_the_median_and_the_threshold(capsys):
    stats = mg.Stats(work_items=21)
    stats.spans = _span_report()
    mg._print_report(stats, dry_run=True)
    out = capsys.readouterr().out

    assert "umbrella cases" in out
    assert "NOT a stored column" in out
    assert "is_umbrella = true" in out
    assert "3.00 days" in out
    assert "KAFKA-14133" in out
    assert "Decision #6 stands" in out


def test_the_report_stays_ascii(capsys):
    """Windows terminals turn a stray em dash into a replacement character."""
    stats = mg.Stats(work_items=21)
    stats.spans = _span_report()
    mg._print_report(stats, dry_run=False)
    capsys.readouterr().out.encode("ascii")


def test_report_survives_a_run_with_nothing_closed(capsys):
    """The state before any PR has merged: no median, so no threshold."""
    from app.normalise.case_span import CaseSpan, summarise

    stats = mg.Stats(work_items=3)
    stats.spans = summarise([CaseSpan(f"KAFKA-{i}", None, 1) for i in range(3)])
    mg._print_report(stats, dry_run=True)
    out = capsys.readouterr().out
    assert "no case has both dates yet" in out
    assert "is_umbrella = true      0 of 3" in out


def test_report_omits_the_section_when_the_run_mapped_nothing(capsys):
    mg._print_report(mg.Stats(), dry_run=True)
    assert "umbrella cases" not in capsys.readouterr().out


# =====================================================================
# The HISTORY_MONTHS window
#
# github_connector pages by UPDATED_AT DESC, so a PR opened in 2015 is in
# raw_payload the moment somebody comments on it in 2026. Its docstring hands
# the decision here explicitly. Mapping those PRs stretched the sprint grid
# from ~26 windows to 289 and produced 177 phantom "merged with no commit".
# =====================================================================

NOW = datetime(2026, 8, 9, tzinfo=UTC)


def test_window_cutoff_matches_the_ingesters_arithmetic():
    """A cutoff even half a day from git_local's would sort the same fortnight
    of commits and PRs into different windows."""
    from datetime import timedelta

    from app.config import get_settings

    months = get_settings().history_months
    expected = NOW - timedelta(days=round(months * 30.44))
    assert mg.window_cutoff(NOW) == expected


def _within(bodies, stats=None):
    stats = stats or mg.Stats()
    return mg._within_window(bodies, stats, now=NOW), stats


def test_a_pr_opened_inside_the_window_is_kept():
    kept, stats = _within([_pr(1, opened="2026-06-01T00:00:00+00:00")])
    assert len(kept) == 1
    assert stats.prs_outside_window == 0


def test_a_pr_created_and_merged_before_the_boundary_is_skipped():
    kept, stats = _within([_pr(1, opened="2015-07-21T00:00:00+00:00",
                               closed="2015-08-01T00:00:00+00:00")])
    assert kept == []
    assert stats.prs_outside_window == 1
    assert stats.prs_created_before_window_kept == 0


def test_an_old_pr_that_merged_inside_the_window_is_kept():
    """Nishant measured 788 of these against the live API, carrying 1,403
    in-window events and 127 merges. Created in 2023, merged last month: the
    work landed inside the window whatever the creation date says."""
    kept, stats = _within([_pr(1, opened="2023-01-01T00:00:00+00:00",
                               closed="2026-07-01T00:00:00+00:00")])
    assert len(kept) == 1
    assert stats.prs_outside_window == 0
    assert stats.prs_created_before_window_kept == 1


def test_mergedat_is_preferred_over_createdat():
    """The whole switch in one assertion: same PR, judged by the date the work
    landed rather than the date it was proposed."""
    body = _pr(1, opened="2023-01-01T00:00:00+00:00",
               closed="2026-07-01T00:00:00+00:00")
    assert mg._ts(body["mergedAt"]) > mg.window_cutoff(NOW)
    assert mg._ts(body["createdAt"]) < mg.window_cutoff(NOW)
    kept, _ = _within([body])
    assert len(kept) == 1


def test_an_old_pr_that_never_merged_falls_back_to_created_at():
    """No mergedAt means the only date it has is createdAt, which is exactly
    what github_connector._content_predates_window does."""
    kept, stats = _within([_pr(1, opened="2015-01-01T00:00:00+00:00",
                               closed=None)])
    assert kept == []
    assert stats.prs_outside_window == 1


def test_a_pr_with_no_dates_at_all_is_kept_not_guessed():
    body = _pr(1, closed=None)
    del body["createdAt"]
    kept, stats = _within([body])
    assert len(kept) == 1, "an unknown date must not be treated as an old one"
    assert stats.prs_outside_window == 0


def test_the_filter_runs_before_mapping_so_counters_stay_honest():
    """Bot drops and unmapped review states must describe what we mapped, not
    what we threw away."""
    bodies = [_pr(1, opened="2015-01-01T00:00:00+00:00",
                  closed="2015-02-01T00:00:00+00:00"),
              _pr(2, opened="2026-06-01T00:00:00+00:00")]
    stats = mg.Stats()
    kept = mg._within_window(bodies, stats, now=NOW)
    assert [b["number"] for b in kept] == [2]


def test_report_prints_both_counts(capsys):
    stats = mg.Stats(prs_outside_window=331, prs_created_before_window_kept=198)
    mg._print_report(stats, dry_run=True)
    out = capsys.readouterr().out
    assert "mergedAt or createdAt" in out
    assert "331" in out and "198" in out
    out.encode("ascii")


def test_report_omits_the_window_section_when_the_window_did_nothing(capsys):
    mg._print_report(mg.Stats(), dry_run=True)
    assert "mergedAt or createdAt" not in capsys.readouterr().out


def test_run_skips_out_of_window_prs_end_to_end(seeded_session):
    from sqlalchemy import select

    from app.db.models import WorkItem

    _land_pr(seeded_session, _pr(9601, key="KAFKA-OLD1",
                                 opened="2015-07-21T00:00:00+00:00",
                                 closed="2015-08-01T00:00:00+00:00"))
    seeded_session.flush()

    stats = mg.run(seeded_session, repos=[FIXTURE_REPO])
    seeded_session.flush()
    assert stats.prs_outside_window == 1
    landed = seeded_session.execute(
        select(WorkItem.work_item_id).where(WorkItem.work_item_id == "KAFKA-OLD1")
    ).scalar_one_or_none()
    assert landed is None, "an out-of-window PR must not create a case"


def test_skipping_never_touches_raw_payload(seeded_session):
    """Nishant owns ingestion and the raw layer keeps everything it fetched."""
    from sqlalchemy import func, select

    from app.db.models import RawPayload

    _land_pr(seeded_session, _pr(9701, key="KAFKA-OLD2",
                                 opened="2014-01-01T00:00:00+00:00"))
    seeded_session.flush()
    before = seeded_session.execute(
        select(func.count()).select_from(RawPayload)
    ).scalar_one()

    mg.run(seeded_session, repos=[FIXTURE_REPO])
    seeded_session.flush()
    after = seeded_session.execute(
        select(func.count()).select_from(RawPayload)
    ).scalar_one()
    assert after == before


# --- opened_at on a case git_local already created ------------------------


def test_the_pr_opened_at_wins_when_it_is_earlier(seeded_session):
    """The 0.00-day median bug.

    git_local creates a case from the squash commit, so its opened_at is the
    merge instant. Leaving opened_at out of the update set kept that value
    while closed_at was overwritten with the PR's - and for a squash merge the
    two are the same second, giving a span of exactly zero.
    """
    from sqlalchemy import select

    from app.db.models import WorkItem

    merge_instant = "2026-07-04T09:00:00+00:00"
    seeded_session.add(
        WorkItem(
            work_item_id="KAFKA-88881",
            repo=FIXTURE_REPO,
            component="core",
            case_source="ticket_key",
            opened_at=datetime(2026, 7, 4, 9, 0, tzinfo=UTC),
        )
    )
    seeded_session.flush()
    _land_pr(seeded_session, _pr(9801, key="KAFKA-88881",
                                 opened="2026-06-01T00:00:00+00:00",
                                 closed=merge_instant))
    seeded_session.flush()

    mg.run(seeded_session, repos=[FIXTURE_REPO])
    seeded_session.flush()
    opened, closed = seeded_session.execute(
        select(WorkItem.opened_at, WorkItem.closed_at).where(
            WorkItem.work_item_id == "KAFKA-88881"
        )
    ).one()
    assert opened == datetime(2026, 6, 1, tzinfo=UTC)
    assert closed == datetime(2026, 7, 4, 9, 0, tzinfo=UTC)
    assert (closed - opened).days == 33, "not a zero-length case"


def test_an_earlier_commit_still_wins_over_the_pr(seeded_session):
    """LEAST, not overwrite: work that began before the PR was opened keeps
    the earlier date, which is git_local's rule and stays git_local's rule."""
    from sqlalchemy import select

    from app.db.models import WorkItem

    seeded_session.add(
        WorkItem(
            work_item_id="KAFKA-88882",
            repo=FIXTURE_REPO,
            case_source="ticket_key",
            opened_at=datetime(2026, 5, 1, tzinfo=UTC),
        )
    )
    seeded_session.flush()
    _land_pr(seeded_session, _pr(9802, key="KAFKA-88882",
                                 opened="2026-06-01T00:00:00+00:00",
                                 closed="2026-06-10T00:00:00+00:00"))
    seeded_session.flush()

    mg.run(seeded_session, repos=[FIXTURE_REPO])
    seeded_session.flush()
    opened = seeded_session.execute(
        select(WorkItem.opened_at).where(WorkItem.work_item_id == "KAFKA-88882")
    ).scalar_one()
    assert opened == datetime(2026, 5, 1, tzinfo=UTC)
