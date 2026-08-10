"""
Workforce layer tests.

The two at the top are the ones the brief calls non-negotiable: an employee
with no preference record can never be named, and a named payload can never
carry a performance figure. Both assert against the SERIALISED response rather
than the dataclass, because the serialiser is what actually reaches a screen.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.api.workforce import RecommendRequest, build_recommendations
from app.workforce import matching, requirements, store

# --- fixtures --------------------------------------------------------------


@pytest.fixture()
def seeded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A store in tmp_path, so tests never touch data/workforce.db."""
    db = tmp_path / "workforce.db"
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", db)
    store.ensure_seeded(db)
    return db


def _req(component: str = "apache/kafka/streams", shift: str = "evening", n: int = 2):
    return requirements.derive(component, n, shift)


# --- CONSENT GATE ----------------------------------------------------------


def test_an_employee_without_a_preference_record_is_never_named(seeded):
    """emp-007 has the strongest resume in the seed — 8 years, consensus and
    stream-processing depth — and submitted no preference form. If the gate
    were decorative she would top every backend ranking, so her absence is
    the assertion with teeth."""
    seed = json.loads(store.SEED_PATH.read_text(encoding="utf-8"))
    consented = {p["employeeId"] for p in seed["preferences"]}
    unconsented = [
        p for p in seed["profiles"] if p["employeeId"] not in consented
    ]
    assert unconsented, "seed must contain someone without preferences"

    named = store.named_candidates(seeded)
    named_ids = {c.employee_id for c in named}
    for profile in unconsented:
        assert profile["employeeId"] not in named_ids

    # And not through the API either, on a requirement they would win.
    for component in ("apache/kafka/streams", "apache/kafka/core"):
        body = build_recommendations(
            RecommendRequest(component=component, engineerCount=8, shift="flexible")
        )
        blob = json.dumps(body)
        for profile in unconsented:
            assert profile["name"] not in blob
            assert profile["employeeId"] not in blob


def test_there_is_no_argument_that_turns_the_consent_gate_off(seeded):
    """A flag would be a way through, so there must not be one."""
    import inspect

    for fn in (store.named_candidates, matching.rank, build_recommendations):
        params = set(inspect.signature(fn).parameters)
        assert not params & {
            "include_unconsented",
            "all_employees",
            "override",
            "force",
            "admin",
        }


def test_unconsented_people_are_still_counted_as_anonymous_capacity(seeded):
    """Not naming somebody is not the same as pretending they do not exist."""
    assert store.anonymous_capacity(seeded) == 2


# --- NO PERFORMANCE DATA ---------------------------------------------------

FORBIDDEN = {
    "cycletime",
    "cycletimedays",
    "throughput",
    "reviewcount",
    "reviews",
    "nreviews",
    "itemsmerged",
    "nmerged",
    "merged",
    "capability",
    "capabilityindex",
    "effectiveness",
    "actorhash",
    "cost",
    "costrupees",
    "rank",
    "percentile",
    "productivity",
    "velocity",
}


def _keys(obj, out: set[str]) -> set[str]:
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(k.lower().replace("_", ""))
            _keys(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _keys(v, out)
    return out


def test_a_named_recommendation_carries_no_performance_field(seeded):
    body = build_recommendations(
        RecommendRequest(component="apache/kafka/streams", engineerCount=3,
                         shift="evening")
    )
    for block in ("recommendedEmployees", "alternates"):
        for card in body[block]:
            leaked = _keys(card, set()) & FORBIDDEN
            assert not leaked, f"performance field on a named card: {leaked}"


def test_the_scoring_dimensions_are_exactly_the_permitted_ones(seeded):
    """Adding a sixth weight is how a performance term would arrive."""
    assert set(matching.WEIGHTS) == {
        "skillMatch",
        "experienceMatch",
        "preferenceMatch",
        "availabilityMatch",
        "projectFamiliarity",
    }
    assert round(sum(matching.WEIGHTS.values()), 6) == 1.0


def test_the_workforce_package_cannot_reach_the_analytics_layer():
    """The join is prevented by there being no import to make it with.

    Parsed from the AST, not grepped: these modules DISCUSS the analytics
    layer at length in their docstrings — explaining what they may not touch
    is most of why they are readable — and a substring check would fail on
    the prose while a real import hidden inside a function would slip past.
    """
    import ast

    banned_roots = {"app.db", "app.models", "app.cost", "app.normalise",
                    "app.ingestion", "sqlalchemy"}
    for module in (store, matching, requirements):
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        for name in imported:
            assert not any(
                name == root or name.startswith(root + ".") for root in banned_roots
            ), f"{module.__name__} imports {name}, which can reach the warehouse"


def test_the_scorer_never_reads_the_declared_staffing_block():
    """Where somebody lives may not move them up or down a ranking.

    `staffing` carries location, willingness to relocate and self-reported
    load. All three are legitimate inputs to a STAFFING DECISION and none is a
    legitimate input to a FIT SCORE — scoring somebody down for declining to
    relocate would turn an honest answer on a form into a quiet penalty. The
    block is therefore routed around `matching.py` by the router rather than
    through it, and this asserts that arrangement instead of trusting it.

    Docstrings stripped, for the same reason as the analytics-layer test: this
    module explains the rule it must not break.
    """
    import ast

    tree = ast.parse(Path(matching.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ) and ast.get_docstring(node):
            node.body = node.body[1:]
    source = ast.unparse(tree)
    for token in (
        "staffing",
        "current_location",
        "preferred_locations",
        "open_to_relocation",
        "current_workload",
        "primary_role",
    ):
        assert token not in source, f"matching.py reads {token!r}, which it may not score"


def test_a_single_person_opening_still_offers_alternates(seeded):
    """n=1 names one person and still shows who else was considered.

    The slice is `fits[:n]` and `fits[n:n+3]`, so n=1 is the case where the
    recommended list is shortest and the alternates matter most — a single
    name with nothing beside it reads as the only candidate rather than as
    the top of a ranking a human is meant to review.
    """
    body = build_recommendations(
        RecommendRequest(component="apache/kafka/clients", engineerCount=1,
                         shift="evening", availability=["mon", "tue", "wed", "thu"])
    )
    assert len(body["recommendedEmployees"]) == 1
    assert len(body["alternates"]) >= 1
    top = body["recommendedEmployees"][0]["matchScore"]
    assert all(a["matchScore"] <= top for a in body["alternates"])
    # The gate does not loosen just because only one person is wanted.
    assert body["anonymousCapacity"]["count"] > 0
    assert body["dataBasis"]["volunteered"] is False


def test_a_named_card_carries_the_declared_staffing_it_needs(seeded):
    """The director's card cannot render without these, and a `{}` staffing
    block would degrade it silently rather than fail."""
    body = build_recommendations(
        RecommendRequest(component="apache/kafka/clients", engineerCount=2,
                         shift="evening")
    )
    cards = body["recommendedEmployees"] + body["alternates"]
    assert cards
    for card in cards:
        s = card["staffing"]
        assert s["currentComponent"].count("/") == 2, s["currentComponent"]
        assert s["currentLocation"]
        assert s["primaryRole"]
        assert s["currentWorkload"] in {"light", "normal", "heavy"}
        assert isinstance(s["openToRelocation"], bool)
        assert card["experienceYears"] > 0


# --- LABELLED AS MODELLED --------------------------------------------------


def test_every_response_says_the_profiles_are_modelled(seeded):
    body = build_recommendations(
        RecommendRequest(component="apache/kafka/core", engineerCount=2)
    )
    assert body["dataBasis"]["volunteered"] is False
    assert body["dataBasis"]["source"] == "modelled"


# --- EXPLANATION IS THE ARITHMETIC ----------------------------------------


def test_contributions_sum_to_the_headline_score(seeded):
    for candidate in store.named_candidates(seeded):
        fit = matching.score(candidate, _req())
        assert round(sum(fit.contributions.values()), 4) == fit.match_score


def test_each_contribution_is_weight_times_sub_score(seeded):
    fit = matching.score(store.named_candidates(seeded)[0], _req())
    for dim, weight in matching.WEIGHTS.items():
        assert fit.contributions[dim] == round(weight * fit.sub_scores[dim], 4)


# --- RANKING RESPONDS TO EMPLOYEE DATA ------------------------------------


def test_two_scenarios_produce_different_top_candidates(seeded):
    """The point of the whole feature: the ranking is a function of employee
    data, not a fixed list. A backend/evening opening and a devops/flexible
    one must not return the same person first."""
    backend = build_recommendations(
        RecommendRequest(component="apache/kafka/streams", engineerCount=1,
                         shift="evening")
    )["recommendedEmployees"]
    devops = build_recommendations(
        RecommendRequest(component="apache/kafka/tools", engineerCount=1,
                         shift="flexible")
    )["recommendedEmployees"]
    assert backend and devops
    assert backend[0]["employeeId"] != devops[0]["employeeId"]


def test_changing_a_declared_preference_changes_the_ranking(seeded):
    """Acceptance criterion 10, proved by mutating the store rather than by
    reasoning about it."""
    req = _req("apache/kafka/tools", shift="flexible", n=1)
    before = matching.rank(store.named_candidates(seeded), req)[0]
    top_before = before[0].employee_id

    # The runner-up declares they are no longer open to cross-team work.
    runner_up = before[1].employee_id
    prefs = {
        "employeeId": runner_up,
        "preferredShift": "morning",
        "workAreas": ["frontend"],
        "availability": ["mon"],
        "workStyle": "individual",
        "openToOtherTeams": False,
    }
    store.save_preferences(runner_up, prefs, seeded)
    after, excluded = matching.rank(store.named_candidates(seeded), req)
    assert runner_up not in {f.employee_id for f in after}
    assert runner_up in {e.employee_id for e in excluded}
    assert after[0].employee_id == top_before


def test_a_skill_change_moves_someone_up(seeded):
    """Same person, same requirement, more matching skills -> higher score.

    Deliberately picks somebody who does NOT already match every required
    skill — the top candidate is at 1.0 by construction, and adding skills to
    a saturated score proves nothing.
    """
    req = _req()
    candidate = min(
        store.named_candidates(seeded),
        key=lambda c: matching.score(c, req).sub_scores["skillMatch"],
    )
    base = matching.score(candidate, req).match_score
    assert matching.score(candidate, req).sub_scores["skillMatch"] < 1.0

    from dataclasses import replace

    improved = replace(
        candidate,
        resume=replace(
            candidate.resume,
            skills=tuple({*candidate.resume.skills, *req.required_skills}),
        ),
    )
    assert matching.score(improved, req).match_score > base


# --- ELIGIBILITY -----------------------------------------------------------


def test_opting_out_of_cross_team_work_excludes_rather_than_downranks(seeded):
    """A high enough score must not be able to override a stated boundary."""
    _, excluded = matching.rank(store.named_candidates(seeded), _req())
    reasons = " ".join(e.reason for e in excluded)
    assert "not open to working across teams" in reasons


def test_a_thin_component_is_flagged_rather_than_guessed(seeded):
    req = requirements.derive("apache/kafka/unassigned", 2, "evening")
    assert req.thin is True
    assert "no skill signal" in req.basis


def test_longest_token_wins_so_flink_table_is_not_table(seeded):
    req = requirements.derive("apache/flink/flink-table", 1)
    assert "SQL" in req.required_skills
    assert req.work_areas == ("data",)


# --- DETERMINISM -----------------------------------------------------------


def test_the_same_scenario_scores_identically_twice(seeded):
    body = RecommendRequest(component="apache/kafka/streams", engineerCount=2,
                            shift="evening")
    assert json.dumps(build_recommendations(body), sort_keys=True) == json.dumps(
        build_recommendations(body), sort_keys=True
    )


# --- a custom opening ------------------------------------------------------


def test_typed_skills_override_the_derived_ones_and_say_so(seeded):
    """A custom opening ranks on what was typed, and the basis records it.

    The distinction has to survive into the response: a list read off a
    component name is a guess, a typed list is a claim somebody is making,
    and the screen prints `basis` under both. If they came back identical
    the screen could not tell a reader which had happened.
    """
    derived = build_recommendations(
        RecommendRequest(component="apache/kafka/clients", engineerCount=2,
                         shift="evening")
    )
    typed = build_recommendations(
        RecommendRequest(component="apache/kafka/clients", engineerCount=2,
                         shift="evening",
                         requiredSkills=["Test Automation", "JUnit", "Java"])
    )
    assert typed["requirement"]["requiredSkills"] == ["Test Automation", "JUnit", "Java"]
    assert typed["requirement"]["basis"] != derived["requirement"]["basis"]
    assert "specified on the opening" in typed["requirement"]["basis"]
    assert typed["requirement"]["thin"] is False


def test_a_custom_opening_reranks_rather_than_returning_the_same_order(seeded):
    """The feature is worthless if the answer does not move."""
    preset = build_recommendations(
        RecommendRequest(component="apache/kafka/clients", engineerCount=2,
                         shift="evening", availability=["mon", "tue", "wed", "thu"])
    )
    custom = build_recommendations(
        RecommendRequest(component="apache/kafka/tools", engineerCount=2,
                         shift="morning",
                         availability=["mon", "tue", "wed", "thu", "fri"],
                         requiredSkills=["Docker", "Kubernetes", "CI/CD"])
    )

    def order(body):
        return [e["name"] for e in body["recommendedEmployees"] + body["alternates"]]

    assert order(preset) != order(custom)


def test_a_custom_opening_changes_none_of_the_privacy_behaviour(seeded):
    """Same gate, same label, same exclusion reporting — a form is an input,
    not a permission."""
    custom = build_recommendations(
        RecommendRequest(component="apache/kafka/tools", engineerCount=2,
                         shift="morning",
                         requiredSkills=["Docker", "Kubernetes", "CI/CD"])
    )
    assert custom["dataBasis"]["volunteered"] is False
    assert custom["anonymousCapacity"]["count"] > 0
    named = {e["name"] for e in custom["recommendedEmployees"] + custom["alternates"]}
    assert "Kavya Iyer" not in named, "the consent gate loosened for a custom opening"
    for card in custom["recommendedEmployees"] + custom["alternates"]:
        assert not (_keys(card, set()) & FORBIDDEN)


def test_blank_skills_still_derive_from_the_component(seeded):
    """An empty list is not a requirement of nothing — it means derive."""
    blank = build_recommendations(
        RecommendRequest(component="apache/kafka/clients", engineerCount=2,
                         shift="evening", requiredSkills=[])
    )
    assert blank["requirement"]["requiredSkills"]
    assert "derived from the component" in blank["requirement"]["basis"]
