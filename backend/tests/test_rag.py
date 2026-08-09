"""
RAG tests — retrieval, ranking, and the guarantees around them.

No database: the workforce layer is deliberately file-backed and separate
from the analytics store, so every test here runs anywhere.

The two that matter most are the ones that prove this is retrieval rather
than a lookup table wearing the name: adding an employee must change other
employees' scores (because IDF is corpus-wide), and editing one employee's
data must change the ranking.
"""

from __future__ import annotations

import dataclasses

import pytest

from app.rag import recommend as rec
from app.rag.recommend import Candidate, recommend
from app.rag.retriever import BM25Index, build_documents, build_query, tokenize
from app.rag.store import Employee, Preferences, Resume, load_employees

# --- fixtures -------------------------------------------------------------


def _emp(eid, name, skills, projects, experience, shift, areas, days):
    return Employee(
        employee_id=eid,
        name=name,
        resume=Resume(projects=tuple(projects), skills=tuple(skills), experience=tuple(experience)),
        preferences=Preferences(
            preferred_shift=shift, work_areas=tuple(areas), availability=tuple(days)
        ),
    )


@pytest.fixture
def people():
    return (
        _emp("employee-a", "Employee A",
             ["Python", "FastAPI", "Distributed Systems", "Backend"],
             ["Rebuilt a broker consumer group protocol handling 40,000 messages per second"],
             ["Four years of backend engineering on streaming infrastructure"],
             "evening", ["backend"], ["mon", "tue", "wed", "thu"]),
        _emp("employee-b", "Employee B",
             ["React", "TypeScript", "Frontend"],
             ["Rebuilt a design system across four product surfaces in React"],
             ["No backend, infrastructure or distributed systems experience"],
             "morning", ["frontend"], ["mon", "tue", "wed", "thu", "fri"]),
        _emp("employee-c", "Employee C",
             ["Docker", "AWS", "DevOps", "Terraform"],
             ["Built a multi-region deployment pipeline with Docker and Terraform on AWS"],
             ["Five years of platform and DevOps engineering"],
             "flexible", ["devops"], ["mon", "tue", "wed", "thu", "fri"]),
    )


SCENARIO_A = dict(
    required_skills=["Python", "Backend", "Distributed Systems"],
    project="Kafka", component="Networking", shift="evening",
    availability=["Monday", "Tuesday", "Wednesday", "Thursday"],
)
SCENARIO_B = dict(
    required_skills=["Docker", "AWS", "DevOps"],
    project="Platform", component="CI", shift="flexible",
    availability=["Monday", "Friday"],
)


def _names(cs: list[Candidate]) -> list[str]:
    return [c.employee.name for c in cs]


# --- the seeded store -----------------------------------------------------


def test_store_has_at_least_three_profiles():
    people = load_employees()
    assert len(people) >= 3
    assert all(e.employee_id and e.name for e in people)
    assert all(e.resume.skills for e in people)


# --- retrieval is real ----------------------------------------------------


def test_retrieval_returns_scored_chunks_with_provenance(people):
    index = BM25Index(build_documents(people))
    hits = index.search(build_query(["Python", "Distributed Systems"], "Kafka"))
    assert hits
    top = hits[0]
    assert top.employee_id == "employee-a"
    assert top.source in {"resume", "preference"}
    assert top.score > 0
    assert top.text  # the actual chunk, not a summary
    # ordered best-first
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)


def test_scores_are_corpus_relative_not_per_document_constants(people):
    """The property a hardcoded ranking cannot have: adding documents changes
    IDF, so an unchanged employee's score moves."""
    q = build_query(["Docker"])
    before = BM25Index(build_documents(people)).search(q)[0].score

    extra = _emp("employee-d", "Employee D", ["Docker"], ["Docker work"], [], "morning", [], ["mon"])
    after_hits = BM25Index(build_documents((*people, extra))).search(q)
    after = next(h.score for h in after_hits if h.employee_id == "employee-c")
    assert after != before


def test_retrieval_finds_nothing_for_an_unrelated_query(people):
    assert BM25Index(build_documents(people)).search("marine biology plankton") == []


def test_tokenizer_keeps_technical_tokens_intact():
    assert "ci/cd" not in tokenize("CI/CD")  # slash splits
    assert "c++" in tokenize("Wrote C++ daemons")
    assert "node.js" in tokenize("Node.js services")


# --- ranking responds to the scenario -------------------------------------


def test_scenario_a_backend_evening_ranks_the_backend_engineer_first(people):
    ranked = recommend(**SCENARIO_A, employees=people, headcount=3)
    assert _names(ranked)[0] == "Employee A"
    assert ranked[0].matched_skills == ["Python", "Backend", "Distributed Systems"]


def test_scenario_b_devops_flexible_ranks_the_devops_engineer_first(people):
    ranked = recommend(**SCENARIO_B, employees=people, headcount=3)
    assert _names(ranked)[0] == "Employee C"
    assert "Docker" in ranked[0].matched_skills


def test_the_two_scenarios_produce_different_winners(people):
    a = recommend(**SCENARIO_A, employees=people, headcount=1)
    b = recommend(**SCENARIO_B, employees=people, headcount=1)
    assert a[0].employee.employee_id != b[0].employee.employee_id


def test_editing_an_employee_changes_the_recommendation(people):
    """The brief's own check: change one person's data, the ranking moves."""
    before = recommend(**SCENARIO_B, employees=people, headcount=3)
    assert _names(before)[0] == "Employee C"

    # Give A the DevOps skills and a flexible shift.
    a = people[0]
    upgraded = dataclasses.replace(
        a,
        resume=dataclasses.replace(a.resume, skills=(*a.resume.skills, "Docker", "AWS", "DevOps")),
        preferences=dataclasses.replace(a.preferences, preferred_shift="flexible",
                                        availability=("mon", "tue", "wed", "thu", "fri")),
    )
    after = recommend(**SCENARIO_B, employees=(upgraded, people[1], people[2]), headcount=3)
    a_before = next(c.match_score for c in before if c.employee.employee_id == "employee-a")
    a_after = next(c.match_score for c in after if c.employee.employee_id == "employee-a")
    assert a_after > a_before


def test_a_preference_change_alone_moves_the_score(people):
    b_morning = recommend(**SCENARIO_A, employees=people, headcount=3)
    b = people[1]
    b_evening = dataclasses.replace(
        b, preferences=dataclasses.replace(b.preferences, preferred_shift="evening")
    )
    after = recommend(**SCENARIO_A, employees=(people[0], b_evening, people[2]), headcount=3)
    before_score = next(c.match_score for c in b_morning if c.employee.employee_id == "employee-b")
    after_score = next(c.match_score for c in after if c.employee.employee_id == "employee-b")
    assert after_score > before_score


def test_ranking_is_deterministic(people):
    a = recommend(**SCENARIO_A, employees=people, headcount=3)
    b = recommend(**SCENARIO_A, employees=people, headcount=3)
    assert [(c.employee.employee_id, c.match_score) for c in a] == [
        (c.employee.employee_id, c.match_score) for c in b
    ]


# --- evidence is never fabricated ----------------------------------------


def test_a_negated_skill_is_not_counted_as_evidence_of_it(people):
    """Regression, and the worst bug this pipeline had: "No backend,
    infrastructure or distributed systems experience" credited a frontend
    engineer with backend AND distributed systems — asserting the opposite of
    what the résumé says."""
    ranked = recommend(**SCENARIO_A, employees=people, headcount=3)
    b = next(c for c in ranked if c.employee.employee_id == "employee-b")
    assert b.matched_skills == []
    assert set(b.missing_skills) == {"Python", "Backend", "Distributed Systems"}


def test_every_evidence_chunk_is_verbatim_from_the_store(people):
    corpus = set()
    for e in people:
        corpus |= set(e.resume.projects) | set(e.resume.experience)
    ranked = recommend(**SCENARIO_A, employees=people, headcount=3)
    for c in ranked:
        for h in c.evidence:
            assert h.kind in {"project", "experience", "skills", "preference"}
            if h.kind in {"project", "experience"}:
                assert h.text in corpus, "evidence text must be quoted, not generated"


def test_weights_are_one_object_and_sum_to_one():
    assert rec.WEIGHTS.total() == pytest.approx(1.0)
    assert rec.WEIGHTS.skills == 0.35
    assert rec.WEIGHTS.resume == 0.25
    assert rec.WEIGHTS.preference == 0.20
    assert rec.WEIGHTS.availability == 0.10
    assert rec.WEIGHTS.familiarity == 0.10


def test_score_breakdown_sums_to_the_match_score(people):
    for c in recommend(**SCENARIO_A, employees=people, headcount=3):
        assert sum(c.breakdown().values()) == pytest.approx(c.match_score, abs=1e-3)


def test_scores_stay_within_zero_and_one(people):
    for c in recommend(**SCENARIO_A, employees=people, headcount=3):
        assert 0.0 <= c.match_score <= 1.0


# --- edge cases -----------------------------------------------------------


def test_no_employees_returns_no_recommendations():
    assert recommend(**SCENARIO_A, employees=()) == []


def test_empty_scenario_does_not_raise(people):
    assert isinstance(recommend(employees=people, headcount=2), list)


def test_weekday_long_and_short_forms_both_match(people):
    long_form = recommend(**{**SCENARIO_A, "availability": ["Monday", "Tuesday"]}, employees=people, headcount=1)
    short_form = recommend(**{**SCENARIO_A, "availability": ["mon", "tue"]}, employees=people, headcount=1)
    assert long_form[0].availability_score == short_form[0].availability_score == 1.0


# --- privacy --------------------------------------------------------------


def test_rag_never_touches_the_analytics_identity_space():
    """Structural: nothing in app/rag may reach the pseudonymised layer.
    Employees here are named; actors there must never be.

    Checked over the AST rather than the raw text, so it inspects what the
    code DOES. A substring scan flags the docstrings that explain the rule
    and would push someone towards deleting the explanation to get green.
    """
    import ast
    import pathlib

    banned_names = {"actor_hash", "get_read_session", "get_read_engine", "event_log"}
    banned_imports = {"sqlalchemy", "app.db", "app.db.session", "app.models.capability_index"}

    for path in sorted(pathlib.Path(rec.__file__).parent.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                assert node.id not in banned_names, f"{path.name} uses {node.id}"
            elif isinstance(node, ast.Attribute):
                assert node.attr not in banned_names, f"{path.name} uses .{node.attr}"
            elif isinstance(node, ast.Import):
                for a in node.names:
                    assert not any(a.name.startswith(b) for b in banned_imports), (
                        f"{path.name} imports {a.name}"
                    )
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert not any(node.module.startswith(b) for b in banned_imports), (
                    f"{path.name} imports from {node.module}"
                )


# --- the endpoint ---------------------------------------------------------


def _client():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


def test_recommend_endpoint_returns_the_contract():
    r = _client().post("/workforce/recommend", json={
        "project": "Kafka", "component": "Networking",
        "requiredSkills": ["Python", "Backend", "Distributed Systems"],
        "shift": "evening",
        "availability": ["Monday", "Tuesday", "Wednesday", "Thursday"],
        "headcount": 2,
    })
    assert r.status_code == 200
    body = r.json()
    assert len(body["recommendations"]) == 2
    top = body["recommendations"][0]
    assert {"employeeId", "name", "matchScore", "skills", "reasons", "evidence",
            "scoreBreakdown"} <= set(top)
    assert top["employeeId"] == "employee-a"
    assert top["evidence"] and all(e["score"] > 0 for e in top["evidence"])
    assert body["weights"]["skills"] == 0.35
    assert "not an assignment" in body["note"]


def test_recommend_endpoint_headcount_limits_the_list():
    r = _client().post("/workforce/recommend", json={"requiredSkills": ["Python"], "headcount": 1})
    assert len(r.json()["recommendations"]) == 1


def test_recommend_endpoint_rejects_a_zero_headcount():
    r = _client().post("/workforce/recommend", json={"requiredSkills": ["Python"], "headcount": 0})
    assert r.status_code == 422


# --- the RAG <-> simulator contract ---------------------------------------
#
# These prove the two halves COMPOSE without JOINING. The pieces that need a
# database take pg_engine and skip when none is listening; the boundary checks
# do not need one and always run.


def test_simulator_scenario_cannot_carry_an_employee_identity():
    """Structural, and the load-bearing one: the simulator's input type has
    no field an employee id could travel in, so no amount of wiring at the
    API layer can make a forecast per-person."""
    import dataclasses

    from app.models.simulator import Scenario

    fields = {f.name for f in dataclasses.fields(Scenario)}
    assert fields == {"source", "destination", "engineer_count"}
    assert not any("employee" in f or "name" in f for f in fields)


def test_staffing_plan_request_reuses_both_existing_contracts():
    """Field names are taken from /simulate and /workforce/recommend rather
    than invented, so a caller reuses a body it already builds."""
    from app.api.simulate import ScenarioRequest
    from app.api.workforce import RecommendRequest, StaffingPlanRequest

    plan = set(StaffingPlanRequest.model_fields)
    assert {"source_project", "dest_project", "engineer_count"} <= plan
    assert set(ScenarioRequest.model_fields) <= plan
    assert {"required_skills", "shift", "availability"} <= set(RecommendRequest.model_fields)


def test_staffing_plan_returns_candidates_and_a_simulation(pg_engine):
    r = _client().post("/workforce/staffing-plan", json={
        "sourceProject": "apache/kafka/streams",
        "destProject": "apache/kafka/core",
        "engineerCount": 2,
        "requiredSkills": ["Python", "Backend", "Distributed Systems"],
        "shift": "evening",
        "availability": ["Monday", "Tuesday", "Wednesday", "Thursday"],
    })
    assert r.status_code == 200, r.text
    body = r.json()

    # 3. RAG returns named candidates
    assert body["recommendedEmployees"], "no candidates returned"
    top = body["recommendedEmployees"][0]
    assert top["employeeId"] and top["name"] and top["evidence"]

    # 4. and the simulation comes back alongside, under the simulator's names
    sim = body["simulation"]
    assert {"sourceDeltaWeeks", "destDeltaWeeks", "netCostRupees",
            "confidenceLow", "confidenceHigh"} <= set(sim)
    assert sim["sourceDeltaWeeks"] > 0 and sim["destDeltaWeeks"] < 0


def test_the_projection_is_identical_whoever_is_recommended(pg_engine, monkeypatch):
    """5 + the privacy claim: swap the entire employee store and the forecast
    must not move. If it ever does, the two layers have been joined."""
    body = {
        "sourceProject": "apache/kafka/streams",
        "destProject": "apache/kafka/core",
        "engineerCount": 2,
        "requiredSkills": ["Python", "Backend"],
        "shift": "evening",
        "availability": ["Monday"],
    }
    first = _client().post("/workforce/staffing-plan", json=body).json()

    only_frontend = (
        _emp("employee-z", "Employee Z", ["React"], ["A React app"], [], "morning", ["frontend"], ["fri"]),
    )
    monkeypatch.setattr("app.api.workforce.recommend",
                        lambda **kw: __import__("app.rag.recommend", fromlist=["recommend"])
                        .recommend(**{**kw, "employees": only_frontend}))
    second = _client().post("/workforce/staffing-plan", json=body).json()

    assert first["recommendedEmployees"] != second["recommendedEmployees"], "store swap had no effect"
    assert first["simulation"] == second["simulation"], "the forecast moved with the candidates"


def test_staffing_plan_surfaces_a_refused_scenario_as_422(pg_engine):
    r = _client().post("/workforce/staffing-plan", json={
        "sourceProject": "apache/kafka/core",
        "destProject": "apache/kafka/core",
        "engineerCount": 1,
        "requiredSkills": ["Python"],
    })
    assert r.status_code == 422
    assert "same component" in r.json()["detail"]


def test_no_employee_name_appears_on_any_analytics_endpoint(pg_engine):
    """7. Named people belong to the workforce layer only."""
    from app.rag.store import load_employees

    names = {e.name.lower() for e in load_employees()} | {
        e.employee_id.lower() for e in load_employees()
    }
    c = _client()
    for path in ("/spend?limit=200", "/spend/summary", "/process/map",
                 "/waste/by-project", "/waste/summary"):
        text = c.get(path).text.lower()
        for name in names:
            assert name not in text, f"{name!r} leaked into {path}"
