"""
Config and app-boot tests.

Also asserts the thing /verify check 3 asks for statically: no column named
login, email, name or salary exists anywhere in the analytics schema. Running
it against the SQL text means it fails in CI before a database exists, which is
where a privacy regression is cheapest to catch.
"""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

from app.config import ASSUMPTION_FIELDS, REPO_ROOT, Settings
from app.main import ROUTER_MODULES, app

IDENTITY_TOKENS = ("login", "email", "name", "salary")


# --- config --------------------------------------------------------------


def test_env_example_keys_all_exist_on_settings():
    """A key in .env.example with no field here is a silently ignored setting."""
    text_ = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    keys = {
        line.split("=", 1)[0].strip().lower()
        for line in text_.splitlines()
        if "=" in line and not line.strip().startswith("#")
    }
    missing = sorted(keys - set(Settings.model_fields))
    assert not missing, f".env.example keys with no Settings field: {missing}"


def test_settings_fields_are_all_documented_in_env_example():
    text_ = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    documented = {
        line.split("=", 1)[0].strip().lower()
        for line in text_.splitlines()
        if "=" in line and not line.strip().startswith("#")
    }
    missing = sorted(set(Settings.model_fields) - documented)
    assert not missing, f"Settings fields absent from .env.example: {missing}"


def test_csv_settings_split_into_lists():
    s = Settings(github_repos="apache/kafka, apache/flink", asf_jira_projects="KAFKA")
    assert s.github_repo_list == ["apache/kafka", "apache/flink"]
    assert s.jira_project_list == ["KAFKA"]


def test_identity_db_path_resolves_from_the_repo_root_not_the_cwd():
    s = Settings(identity_db_path="data/identity.db")
    assert s.identity_db_file == REPO_ROOT / "data" / "identity.db"
    assert s.identity_db_file.is_absolute()


def test_assumptions_are_flagged_as_assumptions():
    """Decision #9: these are exposed as assumptions, never as observed data."""
    assert "meeting_hours_per_week" in ASSUMPTION_FIELDS
    assert set(Settings().assumption_summary()) == set(ASSUMPTION_FIELDS)


def test_session_defaults_match_the_agreed_values():
    s = Settings()
    assert (s.session_gap_minutes, s.session_lead_in_minutes) == (90, 30)
    assert s.session_daily_cap_hours == 10
    assert s.sprint_days == 14


# --- app boot ------------------------------------------------------------


def test_health_endpoint():
    assert TestClient(app).get("/health").json() == {"status": "ok"}


def test_meta_endpoint_reports_provenance_and_assumptions():
    body = TestClient(app).get("/meta").json()
    assert body["data_source"] in {"real", "fixtures"}
    assert body["k_anonymity_floor"] >= 1
    assert "meeting_hours_per_week" in body["assumptions"]


def test_cors_allows_the_vite_dev_server():
    response = TestClient(app).get(
        "/health", headers={"Origin": "http://localhost:5173"}
    )
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_all_four_routers_are_known_even_before_they_exist():
    assert set(ROUTER_MODULES) == {"spend", "waste", "process", "simulate"}


# --- static privacy check ------------------------------------------------


def test_no_identity_column_in_the_frozen_schema(schema_tables):
    offenders = [
        f"{table}.{col.name}"
        for table, cols in schema_tables.items()
        for col in cols
        if any(token in col.name.lower() for token in IDENTITY_TOKENS)
    ]
    assert not offenders, f"identity columns in docs/schema.sql: {offenders}"


def test_no_identity_column_in_the_models():
    from app.db.models import Base

    offenders = [
        f"{table}.{col.name}"
        for table, t in Base.metadata.tables.items()
        for col in t.columns
        if any(token in col.name.lower() for token in IDENTITY_TOKENS)
    ]
    assert not offenders, f"identity columns in models.py: {offenders}"


def test_identity_store_is_only_opened_from_the_ingestion_package():
    """Privacy rule: data/identity.db is OPENED only by backend/app/ingestion/.

    config.py is exempt because it declares the path and nothing more —
    knowing where the file lives is not the same as reading it. What this
    guards against is a router, a cost function or a notebook helper
    constructing the store and joining a login back onto an actor_hash.
    """
    app_dir = REPO_ROOT / "backend" / "app"
    exempt = {app_dir / "config.py"}
    opens_the_store = re.compile(r"IdentityStore\(|identity_store\(|sqlite3\.connect")
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in app_dir.rglob("*.py")
        if path.parts[-2] != "ingestion"
        and path not in exempt
        and opens_the_store.search(path.read_text(encoding="utf-8"))
    ]
    assert not offenders, f"identity store opened outside ingestion/: {offenders}"
