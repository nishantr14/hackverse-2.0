"""
git_local parsing and derivation tests.

All pure-function tests: no clone, no database, no network. The parser is
where the bugs live — a commit subject containing '|', a rename, a binary
file, a merge with two parents — and each of those silently corrupts a case id
or a component rather than raising.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.ingestion.git_local import (
    HEADER,
    Commit,
    _parse_header,
    _parse_numstat,
    _resolve_rename,
    event_id_for,
    identity_key_from_email,
    infer_band,
    infer_tenure,
    read_commits,
    work_item_id_for,
)

TS = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def make_commit(**kw) -> Commit:
    defaults = {
        "sha": "a" * 40,
        "authored_at": TS,
        "committed_at": TS,
        "identity_key": "octocat",
        "subject": "KAFKA-1: fix it",
        "parents": [],
    }
    return Commit(**{**defaults, **kw})


# --- header parsing ------------------------------------------------------


def test_parses_a_plain_header():
    line = f"{HEADER}abc123|2026-03-01T12:00:00+00:00|2026-03-01T12:00:00+00:00|a@b.com|Ada|par1 par2|KAFKA-9: x"
    commit = _parse_header(line)
    assert commit.sha == "abc123"
    assert commit.authored_at == TS
    assert commit.parents == ["par1", "par2"]
    assert commit.subject == "KAFKA-9: x"


def test_subject_containing_a_pipe_does_not_shift_fields():
    """The delimiter appears in real subjects. Subject is last for this reason."""
    line = f"{HEADER}abc|2026-03-01T12:00:00+00:00|2026-03-01T12:00:00+00:00|a@b.com|Ada||KAFKA-9: a|b|c"
    commit = _parse_header(line)
    assert commit.subject == "KAFKA-9: a|b|c"
    assert commit.sha == "abc"


def test_root_commit_has_no_parents():
    line = f"{HEADER}abc|2026-03-01T12:00:00+00:00|2026-03-01T12:00:00+00:00|a@b.com|Ada||initial"
    assert _parse_header(line).parents == []


# --- numstat parsing -----------------------------------------------------


def test_parses_a_numstat_line():
    assert _parse_numstat("10\t4\tcore/src/Foo.java") == (10, 4, "core/src/Foo.java")


def test_binary_files_count_as_zero_not_as_a_crash():
    """git writes '-' for binary files; int('-') would raise."""
    assert _parse_numstat("-\t-\tdocs/logo.png") == (0, 0, "docs/logo.png")


def test_rename_with_braces_resolves_to_the_new_path():
    assert _resolve_rename("core/{old => new}/Foo.java") == "core/new/Foo.java"


def test_plain_rename_resolves_to_the_new_path():
    assert _resolve_rename("old/Foo.java => new/Foo.java") == "new/Foo.java"


def test_malformed_line_is_skipped():
    assert _parse_numstat("garbage")[2] is None


# --- streaming -----------------------------------------------------------


def test_read_commits_streams_multiple_commits(monkeypatch):
    log = "\n".join(
        [
            f"{HEADER}sha1|2026-03-01T12:00:00+00:00|2026-03-01T12:00:00+00:00|a@b.com|Ada||KAFKA-1: one",
            "5\t2\tcore/A.java",
            "1\t0\tcore/B.java",
            "",
            f"{HEADER}sha2|2026-03-02T12:00:00+00:00|2026-03-02T12:00:00+00:00|c@d.com|Bo||FLINK-2: two",
            "-\t-\timg.png",
        ]
    )
    monkeypatch.setattr("app.ingestion.git_local._run_git", lambda *a, **k: log)
    commits = list(read_commits("ignored", "12 months ago"))
    assert [c.sha for c in commits] == ["sha1", "sha2"]
    assert commits[0].files == ["core/A.java", "core/B.java"]
    assert (commits[0].additions, commits[0].deletions) == (6, 2)
    assert commits[1].files == ["img.png"]


# --- component derivation ------------------------------------------------


def test_component_is_the_majority_top_level_directory():
    commit = make_commit(files=["core/A", "core/B", "clients/C"])
    assert commit.component == "core"


def test_root_files_get_the_root_component():
    assert make_commit(files=["build.gradle"]).component == "(root)"


def test_component_ties_break_deterministically():
    """A component that flickers between runs makes every cost figure
    irreproducible, which is worse than picking the 'wrong' one."""
    a = make_commit(files=["zeta/A", "alpha/B"]).component
    b = make_commit(files=["alpha/B", "zeta/A"]).component
    assert a == b == "alpha"


def test_commit_touching_nothing_has_no_component():
    assert make_commit(files=[]).component is None


# --- case id -------------------------------------------------------------


@pytest.mark.parametrize(
    ("subject", "expected"),
    [
        ("KAFKA-16234: fix the thing", "KAFKA-16234"),
        ("[KAFKA-16234] fix the thing", "KAFKA-16234"),
        ("  FLINK-1: leading space", "FLINK-1"),
        ("MINOR: cleanup", None),
        ("fix KAFKA-99 mentioned mid-sentence", None),
    ],
)
def test_ticket_key_extraction(subject, expected):
    assert make_commit(subject=subject).ticket_key == expected


def test_ticket_key_case_uses_the_key_and_records_its_source():
    commit = make_commit(subject="KAFKA-16234: x")
    assert work_item_id_for(commit, "apache/kafka") == ("KAFKA-16234", "ticket_key")


def test_commit_without_a_ticket_key_is_never_dropped():
    """Decision #6: the fallback chain always terminates in a case."""
    commit = make_commit(subject="MINOR: cleanup", sha="deadbeef1234567890")
    work_item_id, case_source = work_item_id_for(commit, "apache/kafka")
    assert work_item_id == "apache/kafka@deadbeef1234"
    assert case_source == "pr"


# --- event id ------------------------------------------------------------


def test_event_id_is_deterministic():
    assert event_id_for("abc", TS) == event_id_for("abc", TS)


def test_event_id_is_24_chars():
    assert len(event_id_for("abc", TS)) == 24


def test_event_id_separates_distinct_commits():
    assert event_id_for("abc", TS) != event_id_for("abd", TS)


# --- identity ------------------------------------------------------------


@pytest.mark.parametrize(
    ("email", "expected"),
    [
        ("1234567+octocat@users.noreply.github.com", "octocat"),
        ("octocat@users.noreply.github.com", "octocat"),
        ("jsmith@apache.org", "jsmith"),
        ("Someone@Example.COM", "someone@example.com"),
    ],
)
def test_identity_key_recovers_a_login_where_the_email_encodes_one(email, expected):
    """Without this, git and the GitHub API produce two actors per person —
    which inflates DISTINCT actor counts and weakens the k floor."""
    assert identity_key_from_email(email, "Display Name") == expected


def test_identity_key_falls_back_to_the_name_when_email_is_missing():
    assert identity_key_from_email("", "Ada Lovelace") == "ada lovelace"


def test_identity_key_never_returns_a_raw_apache_email():
    assert "@" not in identity_key_from_email("jsmith@apache.org", "J Smith")


# --- provisional band / tenure -------------------------------------------


@pytest.mark.parametrize(
    ("commits", "band"),
    [(200, "staff"), (100, "staff"), (50, "senior"), (10, "mid"), (1, "junior")],
)
def test_band_thresholds(commits, band):
    assert infer_band(commits) == band


def test_tenure_short_span_is_lt_6m():
    assert infer_tenure(TS, TS + timedelta(days=30)) == "lt_6m"


def test_tenure_long_span_is_6m_2y():
    assert infer_tenure(TS, TS + timedelta(days=300)) == "6m_2y"


def test_gt_2y_is_never_inferred_from_a_12_month_window():
    """Honesty check: the top bucket is not observable from our window."""
    assert infer_tenure(TS, TS + timedelta(days=365 * 5)) != "gt_2y"


# --- PR-number fallback --------------------------------------------------


@pytest.mark.parametrize(
    ("subject", "expected"),
    [
        ("MINOR: Upgrade gradle to 9.6.1 (#23015)", "23015"),
        ("MINOR: fix thing (#1)", "1"),
        ("MINOR: no pr number here", None),
        ("MINOR: mentions (#123) mid-subject then trails off", None),
    ],
)
def test_pr_number_extraction(subject, expected):
    assert make_commit(subject=subject).pr_number == expected


def test_ticketless_commit_with_a_pr_number_becomes_a_pr_case():
    """721 of 1,841 kafka commits land here. The case COUNT is unchanged —
    Kafka squash-merges — but the id now matches the one the PR connector will
    emit, so reviews and merges join to it instead of forming a parallel case."""
    commit = make_commit(subject="MINOR: Upgrade gradle (#23015)")
    assert work_item_id_for(commit, "apache/kafka") == ("apache/kafka#23015", "pr")


def test_pr_case_id_matches_what_the_github_connector_will_produce():
    """The ids must collide on purpose so the two sources merge."""
    commit = make_commit(subject="MINOR: x (#42)")
    work_item_id, _ = work_item_id_for(commit, "apache/kafka")
    assert work_item_id == "apache/kafka#42"


def test_ticket_key_still_wins_over_a_pr_number():
    commit = make_commit(subject="KAFKA-16234: fix it (#23015)")
    assert work_item_id_for(commit, "apache/kafka") == ("KAFKA-16234", "ticket_key")


def test_commit_with_neither_falls_back_to_the_sha():
    commit = make_commit(subject="no key no pr", sha="deadbeef1234567890")
    assert work_item_id_for(commit, "apache/kafka") == (
        "apache/kafka@deadbeef1234",
        "pr",
    )


def test_author_and_commit_dates_are_parsed_separately():
    """A rebased Apache patch carries an author date years before its commit
    date; conflating them stretched the sprint grid from 26 windows to 63."""
    line = (
        f"{HEADER}abc|2024-03-17T09:00:00+00:00|2026-03-01T12:00:00+00:00"
        "|a@b.com|Ada||FLINK-1: rebased patch"
    )
    commit = _parse_header(line)
    assert commit.authored_at.year == 2024
    assert commit.committed_at.year == 2026
