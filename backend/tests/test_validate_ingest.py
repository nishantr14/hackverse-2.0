"""
Tests for scripts/validate_ingest.py.
Owner: Dipen (normalise + models lane).

The script is split into `Probe` (all SQL, no logic) and `evaluate_*` (all
logic, no SQL) precisely so this file can exist. Every test below feeds a
small fixture dataset — plain tuples shaped exactly like the rows the Probe
returns — straight into an evaluator and asserts the verdict. No Postgres, no
Docker, no ingested data.

That matters for a specific reason: a validator you have not seen fail is not
a validator. These tests prove each check goes red on data that is actually
broken, so the green run against production means something.

One live test at the bottom takes `pg_engine` and SKIPS when nothing is
listening, matching the convention in conftest.py.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "validate_ingest.py"


def _load_script():
    """Import scripts/validate_ingest.py by path.

    `scripts/` is not a package and must not become one just to be importable
    — it is a directory of things you run, not things you import. Loading by
    path keeps that true and keeps sys.path clean.
    """
    spec = importlib.util.spec_from_file_location("validate_ingest", SCRIPT_PATH)
    assert spec and spec.loader, f"cannot load {SCRIPT_PATH}"
    module = importlib.util.module_from_spec(spec)
    sys.modules["validate_ingest"] = module
    spec.loader.exec_module(module)
    return module


vi = _load_script()

T0 = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)


def _hours(n: int) -> datetime:
    return T0 + timedelta(hours=n)


# --- fixture dataset ------------------------------------------------------
# A healthy ingest in miniature: five tables populated, four components, a
# spread of sprints. Every "broken" test below mutates one thing about this.


def healthy_counts() -> dict[str, int]:
    return {
        "run_config": 11,
        "raw_payload": 900,
        "actor": 40,
        "work_item": 300,
        "event_log": 900,
        "ingest_cursor": 2,
        "rate_card": 0,
        "work_session": 0,
        "cost_event": 0,
        "ci_run": 0,
        "calendar_event": 0,
        "ai_usage": 0,
        "variant": 0,
        "code_churn": 0,
        "backtest_result": 0,
    }


# --- check 1: row counts --------------------------------------------------


def test_row_counts_pass_on_a_healthy_ingest():
    result = vi.evaluate_row_counts(healthy_counts())
    assert result.status == vi.PASS


def test_row_counts_fail_when_a_required_table_is_empty():
    counts = healthy_counts() | {"event_log": 0}
    result = vi.evaluate_row_counts(counts)
    assert result.status == vi.FAIL
    assert "event_log" in result.headline


def test_row_counts_tolerate_empty_tier_one_tables():
    """cost_event empty at hour 6 is correct, not broken."""
    result = vi.evaluate_row_counts(healthy_counts())
    assert result.status == vi.PASS
    assert all(counts_row[0] != "cost_event" or counts_row[1] == "0" for counts_row in result.table)


def test_row_counts_fail_when_a_schema_table_is_absent():
    counts = healthy_counts()
    del counts["actor"]
    result = vi.evaluate_row_counts(counts)
    assert result.status == vi.FAIL


def test_row_counts_flag_more_work_items_than_events():
    """Cases with no events vanish from every sequence view — catch it here."""
    counts = healthy_counts() | {"event_log": 10, "work_item": 300}
    result = vi.evaluate_row_counts(counts)
    assert result.status == vi.FAIL
    assert any("event_log" in line and "work_item" in line for line in result.detail)


def test_row_counts_flag_duplicate_ingestion():
    counts = healthy_counts() | {"run_config": 10_000}
    result = vi.evaluate_row_counts(counts)
    assert result.status == vi.FAIL
    assert any("IMPLAUSIBLE" in line for line in result.detail)


# --- check 2: merge ordering ----------------------------------------------


def test_merge_order_passes_when_every_merge_follows_a_commit():
    result = vi.evaluate_merge_order([], n_merged=120)
    assert result.status == vi.PASS


def test_merge_order_is_a_warning_not_a_pass_when_no_merges_exist():
    """The regression that matters: a vacuous check must not read as green.

    With zero merged events the condition is trivially satisfied. Reporting
    PASS would tell the team the commit-to-PR mapping is verified when in fact
    nothing has been verified at all.
    """
    result = vi.evaluate_merge_order([], n_merged=0)
    assert result.status == vi.WARN
    assert "VACUOUS" in result.headline


def test_merge_order_reports_every_offending_work_item_id():
    """The ids are the deliverable here, so they must not be truncated."""
    offenders = [
        (f"KAFKA-{i}", _hours(1), _hours(5), "merged_before_first_commit")
        for i in range(60)
    ]
    result = vi.evaluate_merge_order(offenders, n_merged=60)
    assert result.status == vi.FAIL
    printed = "\n".join(result.detail)
    for i in (0, 37, 59):
        assert f"KAFKA-{i}" in printed, "offending ids must never be truncated"


def test_merge_order_separates_the_two_breakage_shapes():
    offenders = [
        ("A-1", _hours(1), _hours(5), "merged_before_first_commit"),
        ("A-2", _hours(1), None, "merged_with_no_commit"),
        ("A-3", _hours(1), None, "merged_with_no_commit"),
    ]
    result = vi.evaluate_merge_order(offenders, n_merged=3)
    assert result.status == vi.FAIL
    joined = "\n".join(result.detail)
    assert "1 work item(s) merged BEFORE" in joined
    assert "2 work item(s) merged with NO commit" in joined


# --- check 3: actor referential integrity ---------------------------------


def test_orphan_actors_pass_when_none():
    assert vi.evaluate_orphan_actors([]).status == vi.PASS


def test_orphan_actors_fail_and_sample():
    result = vi.evaluate_orphan_actors([f"hash{i:04d}" for i in range(50)])
    assert result.status == vi.FAIL
    assert "50" in result.headline
    assert any("and 30 more" in line for line in result.detail)


# --- check 4: activity vocabulary -----------------------------------------

VOCAB = frozenset(
    {
        "commit",
        "review_requested",
        "review",
        "changes_requested",
        "approved",
        "merged",
        "reopened",
        "force_push",
        "ci_run",
        "deploy",
        "ticket_created",
        "ticket_started",
        "ticket_in_review",
        "ticket_resolved",
        "ticket_closed",
        "ticket_reopened",
        "meeting",
    }
)


def test_activity_vocabulary_passes_on_known_activities():
    result = vi.evaluate_activity_vocabulary({"commit": 900, "merged": 120}, VOCAB)
    assert result.status == vi.PASS


def test_activity_vocabulary_fails_on_the_banned_spellings():
    """Decision #14: `merged` not `merge`, `approved` not `approve`."""
    result = vi.evaluate_activity_vocabulary({"merge": 5, "approve": 3}, VOCAB)
    assert result.status == vi.FAIL
    joined = "\n".join(result.detail)
    assert "'approve'" in joined and "'merge'" in joined


def test_activity_vocabulary_reports_unused_without_failing():
    result = vi.evaluate_activity_vocabulary({"commit": 10}, VOCAB)
    assert result.status == vi.PASS
    assert any("never observed" in line for line in result.detail)


def test_vocabulary_is_parsed_from_the_frozen_schema():
    """Check 4 must read docs/schema.sql, not a copy that can drift."""
    schema_sql = (REPO_ROOT / "docs" / "schema.sql").read_text(encoding="utf-8")
    parsed = vi.parse_check_vocabulary(schema_sql, "activity")
    assert parsed == VOCAB
    assert vi.parse_check_vocabulary(schema_sql, "source") == {
        "github",
        "jira",
        "synthetic",
    }


def test_parsing_a_missing_check_raises_rather_than_defaulting():
    with pytest.raises(ValueError, match="no CHECK"):
        vi.parse_check_vocabulary("CREATE TABLE t (a TEXT);", "activity")


# --- check 5: source ------------------------------------------------------

SOURCES = frozenset({"github", "jira", "synthetic"})


def test_source_passes_when_every_row_has_one():
    result = vi.evaluate_source({"github": 800, "jira": 100}, SOURCES)
    assert result.status == vi.PASS


def test_source_fails_on_nulls():
    """Decision #11: a row without a source is a bug."""
    result = vi.evaluate_source({"github": 800, None: 3}, SOURCES)
    assert result.status == vi.FAIL
    assert "null source" in result.headline


def test_source_fails_on_values_outside_the_enum():
    result = vi.evaluate_source({"github": 800, "gitlab": 2}, SOURCES)
    assert result.status == vi.FAIL
    assert "gitlab" in result.headline


# --- check 6: sprint ------------------------------------------------------


def test_sprint_present_passes():
    assert vi.evaluate_sprints_present([], 300).status == vi.PASS


def test_sprint_missing_fails_with_a_sample():
    result = vi.evaluate_sprints_present(["W-1", "W-2"], 300)
    assert result.status == vi.FAIL
    assert "2" in result.headline and "300" in result.headline


# --- check 7: identity columns --------------------------------------------


def test_identity_check_passes_on_the_real_schema_shape():
    columns = {
        "event_log": ["event_id", "work_item_id", "actor_hash", "activity", "ts"],
        "actor": ["actor_hash", "role_band", "tenure_bucket"],
        "work_item": ["work_item_id", "repo", "component", "sprint"],
    }
    assert vi.evaluate_no_identity_columns(columns).status == vi.PASS


@pytest.mark.parametrize(
    "leaky_column", ["login", "author_email", "display_name", "salary_band"]
)
def test_identity_check_fails_on_each_identity_token(leaky_column):
    columns = {"actor": ["actor_hash", leaky_column]}
    result = vi.evaluate_no_identity_columns(columns)
    assert result.status == vi.FAIL
    assert "actor" in "\n".join(result.detail)


def test_identity_check_names_the_offending_relation():
    """Per-relation so the message says WHERE, not just THAT."""
    columns = {
        "clean_table": ["actor_hash"],
        "leaky_view": ["actor_hash", "login"],
    }
    result = vi.evaluate_no_identity_columns(columns)
    assert result.status == vi.FAIL
    joined = "\n".join(result.detail)
    assert "leaky_view" in joined and "clean_table" not in joined


def test_identity_check_uses_the_shared_rule_not_a_local_copy():
    """The token list must live in pseudonymize.py and nowhere else."""
    from app.ingestion.pseudonymize import IDENTITY_TOKENS

    source = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "assert_no_identity" in source
    for token in IDENTITY_TOKENS:
        assert f'"{token}"' not in source, (
            f"validate_ingest.py restates the identity token {token!r}; it must "
            "reuse IDENTITY_TOKENS via assert_no_identity instead"
        )


# --- check 8: k-anon survival ---------------------------------------------
# rows are (component, n_actors, n_items, n_events)


def test_k_anon_passes_when_a_component_clears_the_floor():
    rows = [("core", 9, 40, 200), ("streams", 5, 20, 90), ("connect", 2, 5, 10)]
    result = vi.evaluate_k_anon_survival(rows, floor=5, fallback=3)
    assert result.status == vi.PASS
    assert "2/3 components survive k=5" in result.headline
    assert "2/3 survive k=3" in result.headline


def test_k_anon_warns_when_only_the_fallback_saves_the_demo():
    rows = [("core", 4, 10, 40), ("streams", 3, 8, 30)]
    result = vi.evaluate_k_anon_survival(rows, floor=5, fallback=3)
    assert result.status == vi.WARN


def test_k_anon_fails_when_nothing_survives_even_the_fallback():
    rows = [("core", 2, 4, 10), ("streams", 1, 2, 5)]
    result = vi.evaluate_k_anon_survival(rows, floor=5, fallback=3)
    assert result.status == vi.FAIL


def test_k_anon_labels_a_null_component_rather_than_dropping_it():
    rows = [(None, 7, 30, 100)]
    result = vi.evaluate_k_anon_survival(rows, floor=5, fallback=3)
    assert result.table[0][0] == "(none)"


def test_k_anon_marks_suppressed_rows_explicitly():
    rows = [("core", 9, 40, 200), ("tiny", 1, 1, 1)]
    result = vi.evaluate_k_anon_survival(rows, floor=5, fallback=3)
    assert result.table[1][4] == "SUPPRESSED"


def test_k_anon_is_rendered_as_a_banner():
    """The team needs this number most; it must not be one row among ten."""
    result = vi.evaluate_k_anon_survival([("core", 9, 40, 200)], floor=5, fallback=3)
    assert result.banner is True
    rendered = vi.render([result], "test")
    assert "#" * 78 in rendered
    assert "K-ANON SURVIVAL" in rendered


def test_k_anon_prints_both_thresholds():
    result = vi.evaluate_k_anon_survival([("core", 9, 40, 200)], floor=5, fallback=3)
    joined = "\n".join(result.detail)
    assert "K_ANONYMITY_FLOOR    = 5" in joined
    assert "K_ANONYMITY_FALLBACK = 3" in joined


# --- check 9: review latency coverage -------------------------------------


def test_review_latency_passes_at_the_threshold():
    result = vi.evaluate_review_latency(40, 100)
    assert result.status == vi.PASS


def test_review_latency_fails_just_below_the_threshold():
    result = vi.evaluate_review_latency(39, 100)
    assert result.status == vi.FAIL
    assert "39.0%" in result.headline


def test_review_latency_fails_on_an_empty_log():
    assert vi.evaluate_review_latency(0, 0).status == vi.FAIL


# --- check 10: trainable sprint windows -----------------------------------


def test_sprint_windows_pass_with_enough_populated_windows():
    rows = [(i, 25) for i in range(vi.MIN_SPRINTS_FOR_TRAINING)]
    assert vi.evaluate_sprint_windows(rows).status == vi.PASS


def test_sprint_windows_fail_when_windows_are_too_thin():
    rows = [(i, vi.MIN_ITEMS_PER_SPRINT - 1) for i in range(26)]
    result = vi.evaluate_sprint_windows(rows)
    assert result.status == vi.FAIL
    assert "0/26 sprint windows" in result.headline


def test_sprint_windows_fail_with_too_few_windows():
    rows = [(i, 100) for i in range(vi.MIN_SPRINTS_FOR_TRAINING - 1)]
    assert vi.evaluate_sprint_windows(rows).status == vi.FAIL


def test_sprint_windows_print_the_chosen_thresholds():
    result = vi.evaluate_sprint_windows([(0, 25)])
    assert any("MIN_ITEMS_PER_SPRINT" in line for line in result.detail)


# --- check 11: umbrella case spans ----------------------------------------
#
# Rows are shaped as Probe.case_spans returns them: (work_item_id,
# case_source, days_or_None).


def _span_row(name: str, days: float | None, source: str = "ticket_key") -> tuple:
    return (name, source, days)


def _typical(n: int = 20) -> list[tuple]:
    return [_span_row(f"KAFKA-{i}", 3.0) for i in range(n)]


def test_case_spans_pass_when_nothing_is_an_outlier():
    result = vi.evaluate_case_spans(_typical())
    assert result.status == vi.PASS
    assert "30.0 days" in result.headline


def test_case_spans_warn_never_fail():
    """The whole point of check 11: this is a property of open-source data,
    not a pipeline bug, so it must not block a build."""
    rows = _typical() + [_span_row("KAFKA-14133", 520.0)]
    result = vi.evaluate_case_spans(rows)
    assert result.status == vi.WARN
    assert vi.exit_code([result]) == 0


def test_case_spans_report_the_count_and_the_ticket_keys():
    rows = _typical() + [
        _span_row("KAFKA-14133", 520.0),
        _span_row("KAFKA-10199", 480.0),
    ]
    result = vi.evaluate_case_spans(rows)
    assert "2 of 22 cases" in result.headline
    keys = [row[0] for row in result.table]
    assert keys == ["KAFKA-14133", "KAFKA-10199"], "longest span first"


def test_case_spans_show_at_most_ten_and_say_how_many_were_hidden():
    rows = _typical() + [_span_row(f"KAFKA-9{i:03d}", 400.0 + i) for i in range(15)]
    result = vi.evaluate_case_spans(rows)
    assert len(result.table) == vi.UMBRELLA_TOP_N
    assert any("top 10" in line and "15 flagged" in line for line in result.detail)


def test_case_spans_print_the_multiple_and_the_median():
    result = vi.evaluate_case_spans(_typical() + [_span_row("KAFKA-1", 900.0)])
    text = " ".join(result.detail)
    assert "10x the median" in text
    assert "3.00 days" in text


def test_case_spans_honour_a_custom_multiple():
    # median 3 days, so the case sits under 10x (30d) and over 5x (15d).
    rows = _typical() + [_span_row("KAFKA-1", 25.0)]
    assert vi.evaluate_case_spans(rows, multiple=10.0).status == vi.PASS
    assert vi.evaluate_case_spans(rows, multiple=5.0).status == vi.WARN


def test_case_spans_are_vacuous_before_the_pr_mapper_runs():
    """git_local's provisional cases carry an opened_at and no closed_at, so
    there is no median. That must read as unproven, not as passed."""
    rows = [_span_row(f"apache/kafka@{i}", None, "pr") for i in range(50)]
    result = vi.evaluate_case_spans(rows)
    assert result.status == vi.WARN
    assert "VACUOUS" in result.headline


def test_case_spans_are_vacuous_on_an_empty_table():
    result = vi.evaluate_case_spans([])
    assert result.status == vi.WARN
    assert "VACUOUS" in result.headline


def test_case_spans_say_decision_six_is_unchanged():
    """A reader seeing this warning must not conclude the merge rule is wrong."""
    result = vi.evaluate_case_spans(_typical() + [_span_row("KAFKA-1", 900.0)])
    assert any("Decision #6 stands" in line for line in result.detail)


def test_case_spans_use_the_shared_rule_not_a_local_copy():
    """If this check and the mapper ever disagree about what an umbrella is,
    the two reports contradict each other on stage."""
    from app.normalise import case_span

    assert vi.UMBRELLA_SPAN_MULTIPLE is case_span.UMBRELLA_SPAN_MULTIPLE
    assert vi.summarise is case_span.summarise


# --- exit codes and rendering ---------------------------------------------


def _result(number: int, status: str) -> object:
    return vi.CheckResult(number, f"check {number}", status, "headline")


def test_exit_code_zero_when_everything_passes():
    assert vi.exit_code([_result(1, vi.PASS), _result(2, vi.PASS)]) == 0


def test_exit_code_zero_when_only_warnings():
    assert vi.exit_code([_result(1, vi.PASS), _result(2, vi.WARN)]) == 0


def test_exit_code_one_on_any_failure():
    assert vi.exit_code([_result(1, vi.PASS), _result(2, vi.FAIL)]) == 1


def test_exit_code_two_when_checks_could_not_run():
    assert vi.exit_code([_result(1, vi.PASS), _result(2, vi.SKIP)]) == 2


def test_failure_beats_skip_in_the_exit_code():
    assert vi.exit_code([_result(1, vi.FAIL), _result(2, vi.SKIP)]) == 1


def test_render_names_the_failing_check_numbers():
    rendered = vi.render([_result(1, vi.PASS), _result(7, vi.FAIL)], "test")
    assert "FAILED CHECKS: 7" in rendered
    assert "do not build views on this data" in rendered


def test_render_is_ascii_only():
    """The team is on Windows terminals; a non-ASCII byte becomes a mojibake."""
    result = vi.evaluate_row_counts(healthy_counts())
    rendered = vi.render([result], "validator")
    rendered.encode("ascii")  # raises UnicodeEncodeError on regression


# --- live database --------------------------------------------------------


def test_full_run_against_a_live_database(pg_engine):
    """End-to-end smoke test. Skips when Postgres is not running."""
    schema_sql = (REPO_ROOT / "docs" / "schema.sql").read_text(encoding="utf-8")
    engine, label = vi.build_engine(app_engine=False)
    with engine.connect() as conn:
        results = vi.run_checks(vi.Probe(conn), schema_sql, app_engine=False)

    assert [r.number for r in results] == list(range(1, 12))
    assert all(r.status in {vi.PASS, vi.FAIL, vi.WARN, vi.SKIP} for r in results)
    vi.render(results, label).encode("ascii")


def test_the_validator_cannot_write(pg_engine):
    """The engine swap is only defensible if it really cannot mutate."""
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    engine, _ = vi.build_engine(app_engine=False)
    with engine.connect() as conn, pytest.raises(DBAPIError) as excinfo:
        conn.execute(text("INSERT INTO run_config (key, value) VALUES ('x', 'y')"))
    assert "read-only" in str(excinfo.value).lower()
