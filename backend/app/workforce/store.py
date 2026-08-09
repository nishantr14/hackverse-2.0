"""
Workforce profile store — volunteered data, physically outside the warehouse.

WHY THIS IS NOT A POSTGRES TABLE
    `docs/schema.sql` is the analytics database: pseudonymised, observed, and
    printed on screen as carrying no per-person view. Employee names in that
    same database would make "the two layers are never joined" a promise kept
    by everyone remembering not to write the join. Here it is kept by there
    being no join to write — the profiles are in a different database, reached
    through a different session, and this module imports nothing from
    `app.db`. That mirrors `data/identity.db`, which keeps the login->hash map
    out of the warehouse for exactly the same reason.

THE CONSENT GATE IS A TYPE, NOT A FLAG
    `Candidate` carries non-optional `preferences`. A profile with no
    preference record cannot be turned into one — not by a caller with the
    right argument, because there is no argument, and not by an admin role,
    because there is no role. `named_candidates()` is the ONLY constructor of
    a named record in this package, and it inner-joins preferences.

    Everyone else stays `anonymous_capacity()`: a count and nothing else. That
    is the same thing the simulator has always modelled, so an employee who
    never filled the form is not excluded from the product, only from being
    named in it.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

#: Beside data/identity.db, and gitignored for the same reason.
DEFAULT_DB_PATH = Path(__file__).resolve().parents[3] / "data" / "workforce.db"
SEED_PATH = Path(__file__).resolve().parent / "seed_profiles.json"

#: Every profile in the seed is invented. Returned on every API response so a
#: screen can never present a modelled preference as a volunteered one.
DATA_BASIS = {
    "source": "modelled",
    "volunteered": False,
    "label": "Modelled profiles — not volunteered",
    "note": (
        "Our contributors are pseudonymised Apache accounts who filled in no "
        "preference form and supplied no resume. These profiles are synthetic, "
        "the same way meeting hours and AI token volume are, and are labelled "
        "rather than presented as submissions."
    ),
}


@dataclass(frozen=True)
class Resume:
    skills: tuple[str, ...]
    projects: tuple[str, ...]
    experience: tuple[str, ...]
    years_experience: float
    #: Components this person says they have worked on. DECLARED, not observed
    #: — it is read off the resume, never off the event log, because the join
    #: that would let us check it is the one we do not do.
    familiar_components: tuple[str, ...]


@dataclass(frozen=True)
class Preferences:
    preferred_shift: str
    work_areas: tuple[str, ...]
    availability: tuple[str, ...]
    work_style: str
    open_to_other_teams: bool


@dataclass(frozen=True)
class Candidate:
    """An employee who MAY be named, because they submitted preferences.

    `preferences` is not optional and that is the whole design. There is no
    way to build this object for someone who did not consent.
    """

    employee_id: str
    name: str
    resume: Resume
    preferences: Preferences


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS employee_profile (
    employee_id      TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    resume_json      TEXT NOT NULL
);
-- Separate table, not a nullable column on the profile: the absence of a row
-- here is the absence of consent, and a missing row is harder to fill in by
-- accident than a NULL is.
CREATE TABLE IF NOT EXISTS employee_preferences (
    employee_id      TEXT PRIMARY KEY
                     REFERENCES employee_profile(employee_id) ON DELETE CASCADE,
    preferences_json TEXT NOT NULL,
    submitted_at     TEXT NOT NULL
);
"""


def ensure_seeded(path: Path | None = None) -> Path:
    """Create the store and load the seed once. Idempotent."""
    db = path or DEFAULT_DB_PATH
    with _connect(db) as conn:
        conn.executescript(SCHEMA)
        already = conn.execute("SELECT count(*) FROM employee_profile").fetchone()[0]
        if already:
            return db
        seed = json.loads(SEED_PATH.read_text(encoding="utf-8"))
        conn.executemany(
            "INSERT INTO employee_profile (employee_id, name, resume_json) "
            "VALUES (?, ?, ?)",
            [
                (p["employeeId"], p["name"], json.dumps(p["resume"]))
                for p in seed["profiles"]
            ],
        )
        conn.executemany(
            "INSERT INTO employee_preferences "
            "(employee_id, preferences_json, submitted_at) VALUES (?, ?, ?)",
            [
                (p["employeeId"], json.dumps(p), "2026-08-10T00:00:00Z")
                for p in seed["preferences"]
            ],
        )
    return db


def _resume(raw: dict[str, Any]) -> Resume:
    return Resume(
        skills=tuple(raw.get("skills") or ()),
        projects=tuple(raw.get("projects") or ()),
        experience=tuple(raw.get("experience") or ()),
        years_experience=float(raw.get("yearsExperience") or 0),
        familiar_components=tuple(raw.get("familiarComponents") or ()),
    )


def _preferences(raw: dict[str, Any]) -> Preferences:
    return Preferences(
        preferred_shift=str(raw.get("preferredShift") or "flexible"),
        work_areas=tuple(raw.get("workAreas") or ()),
        availability=tuple(raw.get("availability") or ()),
        work_style=str(raw.get("workStyle") or "mixed"),
        open_to_other_teams=bool(raw.get("openToOtherTeams")),
    )


def named_candidates(path: Path | None = None) -> list[Candidate]:
    """Everyone who may be named. The INNER JOIN is the consent gate.

    A profile with no `employee_preferences` row is not returned, has no code
    path that returns it, and cannot be coerced into one.
    """
    db = ensure_seeded(path)
    with _connect(db) as conn:
        rows = conn.execute(
            "SELECT p.employee_id, p.name, p.resume_json, f.preferences_json "
            "  FROM employee_profile p "
            "  JOIN employee_preferences f USING (employee_id) "
            " ORDER BY p.employee_id"
        ).fetchall()
    return [
        Candidate(
            employee_id=r["employee_id"],
            name=r["name"],
            resume=_resume(json.loads(r["resume_json"])),
            preferences=_preferences(json.loads(r["preferences_json"])),
        )
        for r in rows
    ]


def anonymous_capacity(path: Path | None = None) -> int:
    """How many profiles exist WITHOUT consent. A count, never a name.

    Returned to the UI so the screen can say "4 more people are modelled as
    capacity but cannot be named" — which is what makes the gate visible
    instead of looking like a short list.
    """
    db = ensure_seeded(path)
    with _connect(db) as conn:
        return int(
            conn.execute(
                "SELECT count(*) FROM employee_profile p "
                " WHERE NOT EXISTS (SELECT 1 FROM employee_preferences f "
                "                    WHERE f.employee_id = p.employee_id)"
            ).fetchone()[0]
        )


def save_preferences(
    employee_id: str, prefs: dict[str, Any], path: Path | None = None
) -> str:
    """Record a submission. This is the ONLY way somebody becomes nameable."""
    db = ensure_seeded(path)
    from datetime import UTC, datetime

    stamp = datetime.now(UTC).isoformat()
    with _connect(db) as conn:
        exists = conn.execute(
            "SELECT 1 FROM employee_profile WHERE employee_id = ?", (employee_id,)
        ).fetchone()
        if not exists:
            raise KeyError(f"no employee profile {employee_id!r}")
        conn.execute(
            "INSERT INTO employee_preferences "
            "  (employee_id, preferences_json, submitted_at) VALUES (?, ?, ?) "
            "ON CONFLICT(employee_id) DO UPDATE SET "
            "  preferences_json = excluded.preferences_json, "
            "  submitted_at = excluded.submitted_at",
            (employee_id, json.dumps(prefs), stamp),
        )
    _cached_candidates.cache_clear()
    return stamp


@lru_cache(maxsize=1)
def _cached_candidates() -> tuple[Candidate, ...]:
    return tuple(named_candidates())
