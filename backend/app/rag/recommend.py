"""
Recommendation — retrieved evidence aggregated into a ranked shortlist.
Owner: Livana (RAG integration).
Phase: Tier 3.

    scenario -> query -> retrieve chunks -> group by employee -> score -> rank

The score is a weighted sum of five components, every one of which is read
off real employee data and can be shown on screen next to the number. No
model is trained and nothing is fitted; the weights are a stated policy, not
a learned parameter, and they live in one object so changing the policy is a
one-line change rather than an archaeology exercise.

WHAT "PROJECT FAMILIARITY" IS AND IS NOT. It is whether the employee's own
resume mentions the project or component being staffed. It is NOT
app/models/capability_index.py — that is computed over the pseudonymised
event log, and joining it to a named employee is precisely the thing this
product promises it never does. The two identity spaces stay apart.

Nothing here fabricates evidence. Every `reason` is emitted only when the
fact behind it is present, and every quoted chunk is text that came out of
the store verbatim.

A recommendation is a shortlist for a human, never an assignment. Ordering
is deterministic and ties break on employee id, so the same scenario over
the same store returns the same list in the same order.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.rag.retriever import BM25Index, Hit, build_documents, build_query, tokenize
from app.rag.store import Employee, load_employees


@dataclass(frozen=True)
class Weights:
    """The fit policy. One object; change it here and nowhere else.

    Sums to 1.0 so a score is directly readable as a share of a perfect fit
    rather than an arbitrary point total.
    """

    skills: float = 0.35
    resume: float = 0.25
    preference: float = 0.20
    availability: float = 0.10
    familiarity: float = 0.10

    def total(self) -> float:
        return self.skills + self.resume + self.preference + self.availability + self.familiarity


WEIGHTS = Weights()

#: Weekday vocabulary, matching the frontend contract. Requests may arrive as
#: "Monday" or "mon"; both normalise to the same code rather than one silently
#: failing to match.
_DAYS = {
    "mon": "mon", "monday": "mon",
    "tue": "tue", "tues": "tue", "tuesday": "tue",
    "wed": "wed", "wednesday": "wed",
    "thu": "thu", "thur": "thu", "thurs": "thu", "thursday": "thu",
    "fri": "fri", "friday": "fri",
}


def _day(value: str) -> str | None:
    return _DAYS.get(str(value).strip().lower())


def normalise_days(days) -> set[str]:
    return {d for d in (_day(v) for v in (days or ())) if d}


@dataclass
class Candidate:
    employee: Employee
    matched_skills: list[str] = field(default_factory=list)
    missing_skills: list[str] = field(default_factory=list)
    skill_score: float = 0.0
    resume_score: float = 0.0
    preference_score: float = 0.0
    availability_score: float = 0.0
    familiarity_score: float = 0.0
    evidence: list[Hit] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    @property
    def match_score(self) -> float:
        w = WEIGHTS
        return round(
            w.skills * self.skill_score
            + w.resume * self.resume_score
            + w.preference * self.preference_score
            + w.availability * self.availability_score
            + w.familiarity * self.familiarity_score,
            4,
        )

    def breakdown(self) -> dict[str, float]:
        """Each component's contribution, so a number on screen can be opened
        up rather than taken on trust."""
        w = WEIGHTS
        return {
            "skills": round(w.skills * self.skill_score, 4),
            "resume": round(w.resume * self.resume_score, 4),
            "preference": round(w.preference * self.preference_score, 4),
            "availability": round(w.availability * self.availability_score, 4),
            "familiarity": round(w.familiarity * self.familiarity_score, 4),
        }


def _skill_terms(skill: str) -> set[str]:
    return set(tokenize(skill))


#: Words that turn a sentence into a denial of the skills it names.
#:
#: Without this, "No backend, infrastructure or distributed systems
#: experience" credited a frontend engineer with backend AND distributed
#: systems — the two skills that sentence exists to rule out. Matching
#: keywords against prose reads a claim and its negation identically, which
#: is the single most damaging failure available to an evidence-grounded
#: recommender: it does not merely miss a fit, it asserts the opposite of
#: what the résumé says.
_NEGATIONS = frozenset({"no", "not", "never", "without", "lacks", "lacking", "nor", "none"})

#: Sentence boundaries only. NOT commas: "No backend, infrastructure or
#: distributed systems experience" split on commas would strand
#: "infrastructure or distributed systems experience" as an apparently
#: positive clause, which is the bug wearing a different hat.
_SENTENCE_SPLIT = re.compile(r"[.;!?]+")


def _affirmative_text(chunks: tuple[str, ...] | list[str]) -> str:
    """The parts of a résumé that CLAIM something, with denials removed.

    A whole sentence is dropped when it carries a negation cue, because the
    scope of the negation cannot be resolved reliably without a parser and
    over-removing costs a possible match while under-removing invents one.
    """
    kept: list[str] = []
    for chunk in chunks:
        for sentence in _SENTENCE_SPLIT.split(chunk):
            if not sentence.strip():
                continue
            if _NEGATIONS & set(tokenize(sentence)):
                continue
            kept.append(sentence)
    return " ".join(kept)


def _score_skills(employee: Employee, required: list[str]) -> tuple[float, list[str], list[str]]:
    """Share of required skills evidenced anywhere in the resume.

    Matched against the skills list AND the prose, because "built it in
    Python" is evidence of Python whether or not the word also appears in a
    bullet list. A multi-word requirement counts only if every one of its
    terms appears, so "Distributed Systems" is not satisfied by "systems".
    """
    if not required:
        return 0.0, [], []

    # The skills list and stated work areas are structured fields — a value
    # there is an assertion by construction. Projects and experience are
    # prose, so denials are stripped before they can be read as claims.
    haystack = set(
        tokenize(" ".join([*employee.resume.skills, *employee.preferences.work_areas]))
    ) | set(tokenize(_affirmative_text([*employee.resume.projects, *employee.resume.experience])))
    matched = [s for s in required if _skill_terms(s) and _skill_terms(s) <= haystack]
    missing = [s for s in required if s not in matched]
    return len(matched) / len(required), matched, missing


def _score_preference(employee: Employee, shift: str | None, required_skills: list[str]) -> tuple[float, list[str]]:
    """Shift fit and stated interest. An unstated preference is neutral, not
    a mismatch — silence is not disagreement."""
    p = employee.preferences
    reasons: list[str] = []
    parts: list[float] = []

    if shift:
        want = shift.strip().lower()
        if p.preferred_shift is None:
            parts.append(0.5)
        elif p.preferred_shift == want:
            parts.append(1.0)
            reasons.append(f"Stated preference for {want} shift work")
        elif p.preferred_shift == "flexible":
            parts.append(0.8)
            reasons.append("Stated flexible shift availability, which covers the requirement")
        else:
            parts.append(0.0)
            reasons.append(f"Prefers {p.preferred_shift} shift; the opening is {want}")

    if p.work_areas:
        wanted = set(tokenize(" ".join(required_skills)))
        overlap = [a for a in p.work_areas if _skill_terms(a) & wanted]
        parts.append(1.0 if overlap else 0.0)
        if overlap:
            reasons.append("Asked for work in " + ", ".join(overlap))

    if not p.open_to_other_teams:
        reasons.append("Has said they would rather not move teams — treat as a conversation, not a match")

    return (sum(parts) / len(parts) if parts else 0.5), reasons


def _score_availability(employee: Employee, required_days: set[str]) -> tuple[float, list[str]]:
    if not required_days:
        return 0.5, []
    have = normalise_days(employee.preferences.availability)
    if not have:
        return 0.5, []
    covered = required_days & have
    score = len(covered) / len(required_days)
    if score == 1.0:
        return score, ["Available on every day the opening needs"]
    missing = sorted(required_days - have)
    return score, [f"Not available on {', '.join(missing)}"]


def _score_familiarity(employee: Employee, project: str | None, component: str | None) -> tuple[float, list[str]]:
    """Whether the employee's OWN resume mentions this project or component.

    Deliberately not the capability index — that is per-actor analytics over
    pseudonymised data and must never be attached to a name.
    """
    terms = set(tokenize(" ".join(t for t in (project, component) if t)))
    if not terms:
        return 0.5, []
    haystack = set(tokenize(" ".join(employee.resume.skills))) | set(
        tokenize(_affirmative_text([*employee.resume.projects, *employee.resume.experience]))
    )
    hit = terms & haystack
    if hit:
        return 1.0, ["Resume mentions " + ", ".join(sorted(hit))]
    return 0.0, []


def recommend(
    required_skills: list[str] | None = None,
    project: str | None = None,
    component: str | None = None,
    shift: str | None = None,
    availability: list[str] | None = None,
    headcount: int = 1,
    employees: tuple[Employee, ...] | None = None,
    evidence_per_employee: int = 3,
) -> list[Candidate]:
    """Rank candidates for one opening. Deterministic given the same store."""
    required_skills = list(required_skills or [])
    people = employees if employees is not None else load_employees()
    if not people:
        return []

    index = BM25Index(build_documents(people))
    query = build_query(required_skills, project, component, shift, availability or [])
    hits = index.search(query)

    # Normalise BM25 against the best hit in THIS result set. An absolute
    # score has no ceiling and is not comparable between scenarios; a share of
    # the strongest match in the same run is.
    best = max((h.score for h in hits), default=0.0)
    grouped: dict[str, list[Hit]] = {}
    for h in hits:
        grouped.setdefault(h.employee_id, []).append(h)

    required_days = normalise_days(availability)
    candidates: list[Candidate] = []
    for e in people:
        mine = grouped.get(e.employee_id, [])
        c = Candidate(employee=e)
        c.evidence = mine[:evidence_per_employee]
        # Top chunk, not the sum: a resume with more bullets should not
        # outrank a shorter one that answers the question better.
        c.resume_score = (mine[0].score / best) if mine and best else 0.0

        c.skill_score, c.matched_skills, c.missing_skills = _score_skills(e, required_skills)
        c.preference_score, pref_reasons = _score_preference(e, shift, required_skills)
        c.availability_score, avail_reasons = _score_availability(e, required_days)
        c.familiarity_score, fam_reasons = _score_familiarity(e, project, component)

        if c.matched_skills:
            c.reasons.append("Evidenced " + ", ".join(c.matched_skills))
        if c.missing_skills:
            c.reasons.append("No evidence of " + ", ".join(c.missing_skills))
        c.reasons.extend(pref_reasons)
        c.reasons.extend(avail_reasons)
        c.reasons.extend(fam_reasons)
        candidates.append(c)

    candidates.sort(key=lambda c: (-c.match_score, c.employee.employee_id))
    return candidates[:headcount] if headcount and headcount > 0 else candidates
