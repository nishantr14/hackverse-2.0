"""
Employee store — the seam Diljit's workforce backend replaces.
Owner: Livana (RAG integration).
Phase: Tier 3.

THERE IS NO WORKFORCE BACKEND YET. Checked every branch on the remote at the
time of writing: no employee model, no resume storage, no workforce
endpoint. So rather than invent a second employee database and have two to
reconcile later, everything here goes through ONE function — `load_employees`
— and one JSON file behind it. When the real store lands, that function's
body changes and nothing else in app/rag does.

The shapes mirror the contract already frozen in the frontend
(frontend/src/data/types.ts, WORKFORCE LAYER): the same shift vocabulary,
the same weekday codes, the same split of a resume into projects, skills and
experience. Designing against a different shape and translating later would
be work with no upside.

PRIVACY — THE RULE THAT MAKES THIS LAYER ALLOWED TO EXIST.
Employees here are named. The analytics layer is pseudonymised and must
never be. Nothing in this package imports a database session, touches
event_log, or knows what an actor_hash is: the two identity spaces cannot
meet because there is no code path along which they could. See
.claude/CLAUDE.md, "The workforce layer".
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

#: The stand-in store. Replaced wholesale by the real one; see module note.
SEED_PATH = Path(__file__).with_name("employees.json")


@dataclass(frozen=True)
class Resume:
    projects: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    experience: tuple[str, ...] = ()


@dataclass(frozen=True)
class Preferences:
    """What the employee volunteered. Absent fields mean "no preference
    stated", never "no preference held" — the scorer treats them as neutral
    rather than as a mismatch."""

    preferred_shift: str | None = None
    work_areas: tuple[str, ...] = ()
    availability: tuple[str, ...] = ()
    work_style: str | None = None
    open_to_other_teams: bool = True


@dataclass(frozen=True)
class Employee:
    employee_id: str
    name: str
    resume: Resume = field(default_factory=Resume)
    preferences: Preferences = field(default_factory=Preferences)


def _employee_from(raw: dict[str, Any]) -> Employee:
    r = raw.get("resume") or {}
    p = raw.get("preferences") or {}
    return Employee(
        employee_id=str(raw["employeeId"]),
        name=str(raw.get("name") or raw["employeeId"]),
        resume=Resume(
            projects=tuple(r.get("projects") or ()),
            skills=tuple(r.get("skills") or ()),
            experience=tuple(r.get("experience") or ()),
        ),
        preferences=Preferences(
            preferred_shift=p.get("preferredShift"),
            work_areas=tuple(p.get("workAreas") or ()),
            availability=tuple(p.get("availability") or ()),
            work_style=p.get("workStyle"),
            open_to_other_teams=bool(p.get("openToOtherTeams", True)),
        ),
    )


@lru_cache(maxsize=1)
def load_employees(path: str | None = None) -> tuple[Employee, ...]:
    """Every employee who has volunteered a profile.

    Cached, because the file does not change within a process and rebuilding
    the retrieval index per request would be the slowest thing in the
    pipeline for no benefit. `path` exists so a test can point at a fixture
    without monkeypatching the module.
    """
    src = Path(path) if path else SEED_PATH
    raw = json.loads(src.read_text(encoding="utf-8"))
    return tuple(_employee_from(e) for e in raw["employees"])
