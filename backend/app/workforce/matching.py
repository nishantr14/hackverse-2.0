"""
Step 2 and Step 4 — the fit score, and the ranking built on it.

A WEIGHTED SUM, SO THE WEIGHTS ARE THE EXPLANATION.
    Five sub-scores, each 0..1, each multiplied by a constant from the block
    below. `contributions` returns weight x sub-score per dimension, and those
    numbers sum exactly to the headline score. That is not an approximation of
    why somebody ranked where they did — it IS why, so there is nothing here
    for a feature-attribution library to explain. If this ever becomes a
    trained model the honest move is to say so and stop, not to reach for shap
    to interpret arithmetic.

WHAT THE SCORE MAY SEE, AND WHAT IT MAY NOT
    May: resume skills, resume projects and years, declared work areas,
    declared shift, declared availability, willingness to work across teams,
    and declared component familiarity.

    May NOT: cycle time, throughput, review counts, items merged, capability
    index, or any figure computed by ranking this person against another. All
    of those live in the analytics layer, all of them are keyed by actor_hash,
    and reaching them would need the employee_id -> actor_hash join that this
    product states on screen it does not perform. The capability index is
    doubly out: it is a share measured against an equal split, which is a
    relative ranking of people, which is exactly what a named card must not
    carry. `familiarity` below is therefore DECLARED — read off the resume,
    not measured off the event log.

ELIGIBILITY IS SEPARATE FROM SCORE
    Someone who opted out of cross-team work is not scored badly, they are
    excluded and told why. Ranking them low would let a high enough score
    override a stated boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.workforce.requirements import Requirement
from app.workforce.store import Candidate

# ---------------------------------------------------------------------
# The weights. One block, configurable, summing to 1.0.
# ---------------------------------------------------------------------

SKILL_WEIGHT = 0.35
EXPERIENCE_WEIGHT = 0.25
PREFERENCE_WEIGHT = 0.20
AVAILABILITY_WEIGHT = 0.10
FAMILIARITY_WEIGHT = 0.10

WEIGHTS: dict[str, float] = {
    "skillMatch": SKILL_WEIGHT,
    "experienceMatch": EXPERIENCE_WEIGHT,
    "preferenceMatch": PREFERENCE_WEIGHT,
    "availabilityMatch": AVAILABILITY_WEIGHT,
    "projectFamiliarity": FAMILIARITY_WEIGHT,
}

#: Years at which the experience term saturates. Beyond this more years do not
#: keep buying score — seniority is not the thing being matched.
TARGET_YEARS = 6.0

#: Inside `preferenceMatch`, how much is "do they want this kind of work"
#: versus "does the shift line up". Area first: a shift is negotiable in a way
#: that wanting to do backend work is not.
AREA_SHARE = 0.6
SHIFT_SHARE = 0.4

#: A declared "flexible" shift satisfies any requirement, but is scored just
#: below an exact match so an explicit preference wins a tie.
FLEXIBLE_SHIFT_SCORE = 0.85


@dataclass(frozen=True)
class Fit:
    employee_id: str
    name: str
    match_score: float
    sub_scores: dict[str, float]
    contributions: dict[str, float]
    matched_skills: tuple[str, ...]
    missing_skills: tuple[str, ...]
    skills: tuple[str, ...]
    reasons: tuple[str, ...] = ()
    flags: tuple[str, ...] = ()
    evidence: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class Excluded:
    employee_id: str
    name: str
    reason: str


def _norm(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


def _skill_match(candidate: Candidate, req: Requirement) -> tuple[float, tuple, tuple]:
    if not req.required_skills:
        return 0.0, (), ()
    have = {_norm(s): s for s in candidate.resume.skills}
    matched = tuple(s for s in req.required_skills if _norm(s) in have)
    missing = tuple(s for s in req.required_skills if _norm(s) not in have)
    return len(matched) / len(req.required_skills), matched, missing


def _experience_match(candidate: Candidate, req: Requirement) -> float:
    """Years, saturating, blended with how much of the resume is on topic.

    Years alone would rank a generalist with a decade above a specialist with
    four, which is the wrong answer for a component-shaped opening.
    """
    years = min(1.0, candidate.resume.years_experience / TARGET_YEARS)
    corpus = " ".join((*candidate.resume.projects, *candidate.resume.experience))
    corpus_norm = _norm(corpus)
    if req.required_skills:
        relevant = sum(1 for s in req.required_skills if _norm(s) in corpus_norm)
        topical = relevant / len(req.required_skills)
    else:
        topical = 0.0
    return 0.5 * years + 0.5 * topical


def _preference_match(candidate: Candidate, req: Requirement) -> float:
    prefs = candidate.preferences
    if req.work_areas:
        area = 1.0 if set(prefs.work_areas) & set(req.work_areas) else 0.0
    else:
        # The component maps to no declared work area (docs). Dropping the term
        # is honest; scoring it zero would penalise everyone equally for our
        # taxonomy's gap and quietly reweight the whole model.
        return _shift_score(prefs.preferred_shift, req.preferred_shift)
    return AREA_SHARE * area + SHIFT_SHARE * _shift_score(
        prefs.preferred_shift, req.preferred_shift
    )


def _shift_score(declared: str, required: str) -> float:
    if declared == required:
        return 1.0
    if declared == "flexible" or required == "flexible":
        return FLEXIBLE_SHIFT_SCORE
    return 0.0


def _availability_match(candidate: Candidate, req: Requirement) -> float:
    if not req.required_availability:
        return 1.0
    have = set(candidate.preferences.availability)
    return len(have & set(req.required_availability)) / len(req.required_availability)


def _familiarity_match(candidate: Candidate, req: Requirement) -> float:
    """DECLARED familiarity only — see the module docstring."""
    declared = {_norm(c) for c in candidate.resume.familiar_components}
    if not declared:
        return 0.0
    target = _norm(req.component)
    if target in declared:
        return 1.0
    # A near miss still counts for something: `core` next to `group-coordinator`
    # is adjacent work, and claiming otherwise would be as wrong as claiming
    # it is the same thing.
    if any(d in target or target in d for d in declared if d):
        return 0.5
    return 0.0


def score(candidate: Candidate, req: Requirement) -> Fit:
    """Deterministic. Same candidate and requirement, same bytes out."""
    skill, matched, missing = _skill_match(candidate, req)
    subs = {
        "skillMatch": skill,
        "experienceMatch": _experience_match(candidate, req),
        "preferenceMatch": _preference_match(candidate, req),
        "availabilityMatch": _availability_match(candidate, req),
        "projectFamiliarity": _familiarity_match(candidate, req),
    }
    contributions = {k: round(WEIGHTS[k] * v, 4) for k, v in subs.items()}
    total = round(sum(contributions.values()), 4)

    prefs = candidate.preferences
    reasons: list[str] = []
    flags: list[str] = []
    if matched:
        reasons.append(f"Resume lists {', '.join(matched)}.")
    if missing:
        flags.append(f"No evidence of {', '.join(missing)} on the resume.")
    if subs["experienceMatch"] >= 0.6:
        reasons.append(
            f"{candidate.resume.years_experience:.0f} years, with project history "
            "on this kind of work."
        )
    if prefs.preferred_shift == req.preferred_shift:
        reasons.append(f"Declared {req.preferred_shift} shift, which the opening wants.")
    elif prefs.preferred_shift == "flexible":
        reasons.append("Declared a flexible shift, so the opening's hours work.")
    elif req.preferred_shift != "flexible":
        flags.append(
            f"Prefers the {prefs.preferred_shift} shift; the opening is "
            f"{req.preferred_shift}."
        )
    if subs["availabilityMatch"] == 1.0:
        reasons.append(
            "Available every day the opening needs "
            f"({', '.join(req.required_availability)})."
        )
    elif subs["availabilityMatch"] > 0:
        missing_days = [
            d for d in req.required_availability if d not in prefs.availability
        ]
        flags.append(f"Not available {', '.join(missing_days)}.")
    if subs["projectFamiliarity"] >= 1.0:
        reasons.append(f"Says they have worked on {req.component} before.")
    elif subs["projectFamiliarity"] > 0:
        reasons.append("Has worked on an adjacent component.")

    return Fit(
        employee_id=candidate.employee_id,
        name=candidate.name,
        match_score=total,
        sub_scores={k: round(v, 4) for k, v in subs.items()},
        contributions=contributions,
        matched_skills=matched,
        missing_skills=missing,
        skills=candidate.resume.skills,
        reasons=tuple(reasons),
        flags=tuple(flags),
        evidence={
            "resumeProjects": list(candidate.resume.projects),
            "resumeExperience": list(candidate.resume.experience),
            "declaredShift": prefs.preferred_shift,
            "declaredWorkAreas": list(prefs.work_areas),
            "declaredAvailability": list(prefs.availability),
            "workStyle": prefs.work_style,
            "openToOtherTeams": prefs.open_to_other_teams,
        },
    )


def eligibility(candidate: Candidate, req: Requirement, cross_team: bool) -> str | None:
    """Why this person cannot be proposed, or None. Checked before scoring."""
    if cross_team and not candidate.preferences.open_to_other_teams:
        return "Declared they are not open to working across teams."
    if not set(candidate.preferences.availability) & set(req.required_availability):
        return (
            "Available on none of the days the opening needs "
            f"({', '.join(req.required_availability)})."
        )
    if req.required_skills and not _skill_match(candidate, req)[1]:
        return "Resume shows none of the skills the component needs."
    return None


def rank(
    candidates: list[Candidate], req: Requirement, cross_team: bool = True
) -> tuple[list[Fit], list[Excluded]]:
    """Filter, score, sort. Ties break on employee_id so runs are reproducible."""
    fits: list[Fit] = []
    excluded: list[Excluded] = []
    for candidate in candidates:
        reason = eligibility(candidate, req, cross_team)
        if reason:
            excluded.append(Excluded(candidate.employee_id, candidate.name, reason))
            continue
        fits.append(score(candidate, req))
    fits.sort(key=lambda f: (-f.match_score, f.employee_id))
    return fits, excluded
