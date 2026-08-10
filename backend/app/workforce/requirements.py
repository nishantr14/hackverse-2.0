"""
Step 3 — what kind of person does this scenario actually need?

The scenario the simulator already takes is "move N engineers to
apache/kafka/streams". That names a destination but not a requirement, so this
module turns the destination into one: the skills the work needs, the work
area it sits in, and the schedule being staffed.

DERIVED FROM THE COMPONENT, NOT FROM A PERSON. The taxonomy below maps
component-name tokens to skills. It is a vocabulary, not a recommendation —
nothing here knows an employee exists, so no ranking can be baked into it.
Tokens were taken from the components that actually exist in the event log
(`v_component_capacity`: clients, core, streams, storage, connect, tools,
tests, metadata, group-coordinator, flink-table, flink-runtime, flink-core,
flink-python, docs), not invented.

WHEN WE DO NOT KNOW, WE SAY SO. `unassigned` and `(root)` carry no signal, and
`docs` maps to no declared work area at all — the form offers backend,
frontend, data, devops and testing, and technical writing is none of them.
Rather than forcing a wrong area, the requirement carries an empty
`work_areas`, `matching` drops that term, and the response says the
requirement was thin. Inventing a work area to keep a number tidy is how a
recommendation becomes fiction.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Component-name token -> (skills it implies, declared work areas it fits).
#: Ordered longest-token-first at match time so `flink-table` beats `table`.
COMPONENT_TAXONOMY: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "streams": (("Stream Processing", "Distributed Systems", "Java"), ("backend",)),
    "stream": (("Stream Processing", "Distributed Systems"), ("backend",)),
    "group-coordinator": (("Consensus", "Distributed Systems", "Java"), ("backend",)),
    "coordinator": (("Consensus", "Distributed Systems"), ("backend",)),
    "metadata": (("Consensus", "Distributed Systems"), ("backend",)),
    "storage": (("Storage Systems", "File I/O", "Java"), ("backend",)),
    "clients": (("Client Libraries", "Distributed Systems", "Java"), ("backend",)),
    "consumer": (("Client Libraries", "Distributed Systems"), ("backend",)),
    "producer": (("Client Libraries", "Distributed Systems"), ("backend",)),
    "connect": (("Data Integration", "Java"), ("data", "backend")),
    "flink-table": (("SQL", "Query Engines", "Java"), ("data",)),
    "flink-python": (("Python",), ("backend", "data")),
    "flink-runtime": (("Distributed Systems", "Concurrency", "Java"), ("backend",)),
    "flink-core": (("Distributed Systems", "Java"), ("backend",)),
    "runtime": (("Distributed Systems", "Concurrency"), ("backend",)),
    "server": (("Distributed Systems", "Concurrency", "Java"), ("backend",)),
    "core": (("Distributed Systems", "Concurrency", "Java"), ("backend",)),
    "tools": (("Tooling", "CI/CD"), ("devops",)),
    "tests": (("Test Automation", "CI/CD"), ("testing",)),
    "docs": (("Technical Writing",), ()),
}

#: What the repository itself implies, added to whatever the component says.
REPO_LANGUAGES: dict[str, tuple[str, ...]] = {
    "apache/kafka": ("Java", "Scala"),
    "apache/flink": ("Java", "Scala"),
}

#: Components that carry no signal at all. Named so the response can say the
#: requirement is thin rather than quietly producing a confident empty list.
UNINFORMATIVE = frozenset({"unassigned", "(root)", "", "none"})

WEEKDAYS: tuple[str, ...] = ("mon", "tue", "wed", "thu", "fri")
SHIFTS: frozenset[str] = frozenset({"morning", "afternoon", "evening", "flexible"})


@dataclass(frozen=True)
class Requirement:
    """The opening being staffed. No employee appears in this type."""

    project: str
    component: str
    engineers_required: int
    required_skills: tuple[str, ...]
    work_areas: tuple[str, ...]
    preferred_shift: str
    required_availability: tuple[str, ...]
    #: True when the component told us nothing, so the caller can badge it.
    thin: bool
    basis: str


def _tokens_for(component: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Longest matching token wins, so `flink-table` does not resolve as `table`."""
    lowered = component.lower()
    skills: list[str] = []
    areas: list[str] = []
    for token in sorted(COMPONENT_TAXONOMY, key=len, reverse=True):
        if token in lowered:
            token_skills, token_areas = COMPONENT_TAXONOMY[token]
            skills.extend(token_skills)
            areas.extend(token_areas)
            break
    return tuple(skills), tuple(areas)


def derive(
    component_key: str,
    engineers_required: int,
    shift: str = "flexible",
    availability: tuple[str, ...] | None = None,
    required_skills: tuple[str, ...] | None = None,
) -> Requirement:
    """`component_key` is the simulator's own "repo/component" string.

    `required_skills` is the one input a human can override. Left out, the
    skills are read off the component name and the repo language and `basis`
    says so; supplied, they are taken as stated and `basis` says THAT instead.
    Which of the two happened has to stay visible, because the derived list is
    a guess from a string and a typed list is a claim somebody is making — the
    screen prints the basis line either way and the two must not read alike.
    """
    repo, _, component = component_key.rpartition("/")
    shift = shift if shift in SHIFTS else "flexible"
    days = tuple(d for d in (availability or WEEKDAYS) if d in WEEKDAYS) or WEEKDAYS

    stated = tuple(s.strip() for s in (required_skills or ()) if s.strip())
    if stated:
        # Work areas still come from the component: they map a component to
        # backend/frontend/data/devops/testing, which is a different question
        # from which skills the work needs, and nothing in the form asks it.
        _, areas = _tokens_for(component)
        return Requirement(
            project=repo,
            component=component,
            engineers_required=engineers_required,
            required_skills=stated,
            work_areas=areas,
            preferred_shift=shift,
            required_availability=days,
            thin=False,
            basis="specified on the opening, not derived from the component",
        )

    skills, areas = _tokens_for(component)
    languages = REPO_LANGUAGES.get(repo, ())
    thin = component.lower() in UNINFORMATIVE or not skills

    # Repo languages come second so a component's own skills lead the list the
    # UI prints, and dedupe preserves that order.
    merged: list[str] = []
    for skill in (*skills, *languages):
        if skill not in merged:
            merged.append(skill)

    basis = (
        f"derived from the component name {component!r}"
        + (f" and the {repo} codebase language" if languages else "")
        if not thin
        else (
            f"component {component!r} carries no skill signal — the requirement "
            "falls back to the repository language only, and the skill term is "
            "weak evidence here"
        )
    )
    return Requirement(
        project=repo,
        component=component,
        engineers_required=engineers_required,
        required_skills=tuple(merged),
        work_areas=areas,
        preferred_shift=shift,
        required_availability=days,
        thin=thin,
        basis=basis,
    )
