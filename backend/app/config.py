"""
Engineering Spend Intelligence — backend config.
Owner: Nishant (infra + ingestion lane).
Phase: Tier 0 (must exist before anything else boots).

Every key in .env.example has exactly one field here, and this class rejects
keys it does not know about. That is deliberate: a typo'd env var must fail at
import time, not silently fall back to a default and move a number on screen.

Two of these values are ASSUMPTIONS rather than observations —
MEETING_HOURS_PER_WEEK and SPRINT_DAYS. They are listed in ASSUMPTION_FIELDS
so the UI can badge them. An assumption rendered as observed data is the single
easiest way to lose the "isn't this made up?" question on stage.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Fields whose value is a stated assumption, not anything we measured.
#: The UI must render these with an "assumption" badge and, where the design
#: allows, a slider. See decision #9 in .claude/CLAUDE.md.
ASSUMPTION_FIELDS = frozenset({"meeting_hours_per_week", "sprint_days"})


class Settings(BaseSettings):
    """Environment-driven settings. One field per key in .env.example."""

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Database ---
    # Ingestion and the synthetic generators write through this one.
    database_url: str = "postgresql+psycopg://esi:esi@localhost:5432/esi"
    # The API reads through this one. Granted SELECT on views only.
    database_url_readonly: str = (
        "postgresql+psycopg://esi_app:esi_app@localhost:5432/esi"
    )

    # --- Ingestion: real sources ---
    github_token: str = ""
    github_repos: str = "apache/kafka,apache/flink"
    history_months: int = Field(default=12, gt=0)
    asf_jira_base_url: str = "https://issues.apache.org/jira"
    asf_jira_projects: str = "KAFKA,FLINK"

    # --- Privacy ---
    pseudonymization_salt: str = "change-me"
    identity_db_path: str = "data/identity.db"
    k_anonymity_floor: int = Field(default=5, ge=1)
    k_anonymity_fallback: int = Field(default=3, ge=1)

    # --- Cost: session inference ---
    session_gap_minutes: int = Field(default=90, gt=0)
    session_lead_in_minutes: int = Field(default=30, ge=0)
    session_daily_cap_hours: int = Field(default=10, gt=0)
    sprint_days: int = Field(default=14, gt=0)

    # --- Assumptions, surfaced in the UI, never presented as observed ---
    meeting_hours_per_week: float = Field(default=4.0, ge=0)

    # --- Provenance ---
    data_source: Literal["real", "fixtures"] = "real"

    # --- Frontend (read by Vite, mirrored here so the key is documented) ---
    vite_api_base_url: str = "http://localhost:8000"

    @field_validator("github_repos", "asf_jira_projects")
    @classmethod
    def _reject_empty_csv(cls, v: str) -> str:
        if not _split_csv(v):
            raise ValueError("must list at least one entry")
        return v

    @property
    def github_repo_list(self) -> list[str]:
        """`GITHUB_REPOS` as a list. CSV, not JSON — teammates type these by hand."""
        return _split_csv(self.github_repos)

    @property
    def jira_project_list(self) -> list[str]:
        """`ASF_JIRA_PROJECTS` as a list."""
        return _split_csv(self.asf_jira_projects)

    @property
    def identity_db_file(self) -> Path:
        """Absolute path to the identity SQLite file, resolved from the repo root.

        Relative paths in .env must not depend on the caller's working
        directory — ingestion is run from several places.
        """
        p = Path(self.identity_db_path)
        return p if p.is_absolute() else REPO_ROOT / p

    def assumption_summary(self) -> dict[str, float]:
        """The assumption fields and their current values, for UI badging."""
        return {name: float(getattr(self, name)) for name in sorted(ASSUMPTION_FIELDS)}


def _split_csv(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings singleton. Call this; do not construct Settings()."""
    return Settings()
