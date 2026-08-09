"""
Narrator tests.

The narrator is pure — no database, no network — so all of this runs without
Postgres. The one thing worth guarding hardest is that it cannot invent a
figure, and that is tested by construction rather than by reading the output:
change an input number and the output number must move with it.
"""

from __future__ import annotations

import re

import pytest

from app.narrate import narrator
from app.narrate.narrator import Narrative, build_facts, narrate

SPEND = {
    "totalCost": 84777933.31,
    "totalHours": 39773.9,
    "observedShare": 0.6016,
    "blendedHourly": 1270.69,
    "byBasis": {
        "labour": {"cost": 50540235.33, "kind": "inferred"},
        "ci": {"cost": 463942.63, "kind": "metered"},
        "ai": {"cost": 110364.84, "kind": "modelled"},
    },
}

WASTE = [
    {"type": "meeting", "project": "apache/flink", "component": None,
     "nItems": 319, "hours": 0.0, "amountRupees": 5467112.96},
    {"type": "rework", "project": "apache/kafka", "component": "clients",
     "nItems": 12, "hours": 40.0, "amountRupees": 120000.0},
    # Review latency: the API deliberately returns a null amount.
    {"type": "latency", "project": "apache/kafka", "component": None,
     "nItems": 800, "hours": 5000.0, "amountRupees": None},
]

PROCESS = {
    "workItems": 4949,
    "reworkReturns": {"events": 440, "cases": 299},
    "variants": [
        {"variant": "happy_path", "shareOfWorkItems": 0.665, "shareOfCost": 0.266, "nCases": 3290},
        {"variant": "triple_review", "shareOfWorkItems": 0.275, "shareOfCost": 0.593, "nCases": 1360},
        {"variant": "rework_loop", "shareOfWorkItems": 0.060, "shareOfCost": 0.141, "nCases": 299},
    ],
}

SIMULATION = {
    "sourceDeltaWeeks": 1.2,
    "destDeltaWeeks": -2.18,
    "netCostRupees": 32910.51,
    "confidenceLow": 28.0,
    "confidenceHigh": 72.0,
    "rampUpPenaltyApplied": True,
}


def _numbers(text: str) -> set[str]:
    """Digit groups in a string, ignoring separators."""
    return {m.replace(",", "") for m in re.findall(r"\d[\d,]*", text)}


def _all_prose(n: Narrative) -> str:
    return " ".join([n.summary, *n.findings, n.implication, n.recommendation])


# --- valid metrics --------------------------------------------------------


def test_full_input_produces_every_section():
    n = narrate(spend=SPEND, waste=WASTE, process=PROCESS, simulation=SIMULATION)
    assert n.summary
    assert 2 <= len(n.findings) <= 8
    assert n.implication
    assert n.recommendation
    assert {e.type for e in n.evidence} == {"observed", "modelled", "inferred"}


def test_is_deterministic():
    a = narrate(spend=SPEND, waste=WASTE, process=PROCESS, simulation=SIMULATION)
    b = narrate(spend=SPEND, waste=WASTE, process=PROCESS, simulation=SIMULATION)
    assert a.model_dump() == b.model_dump()


# --- no fabricated numbers ------------------------------------------------


def test_no_number_appears_that_was_not_supplied():
    """Every digit group in the prose must trace to a fact the narrator built,
    and every fact is built from the input. Formatting rescales (84777933 ->
    8.48Cr), so the check is against the rendered facts, which is the only
    surface the renderer is allowed to read."""
    n = narrate(spend=SPEND, waste=WASTE, process=PROCESS, simulation=SIMULATION)
    allowed: set[str] = set()
    for f in n.facts:
        allowed |= _numbers(f.value)
    unexplained = _numbers(_all_prose(n)) - allowed
    assert not unexplained, f"prose contains numbers not in any fact: {unexplained}"


def test_changing_an_input_changes_the_output():
    """The guard against a hardcoded demo figure."""
    a = narrate(spend=SPEND)
    b = narrate(spend={**SPEND, "totalCost": 12345678.0})
    assert a.summary != b.summary
    assert "8.48Cr" in a.summary
    assert "1.23Cr" in b.summary


def test_empty_input_states_nothing_numeric():
    n = narrate()
    assert not _numbers(_all_prose(n)), "an empty request must not produce figures"
    assert n.findings == []
    assert "nothing to explain" in n.summary.lower()
    # And it must not read as "we measured zero problems".
    assert "not a finding" in n.implication.lower()


# --- missing / partial metrics -------------------------------------------


def test_spend_only_does_not_mention_waste_or_process():
    n = narrate(spend=SPEND)
    prose = _all_prose(n).lower()
    assert "8.48cr" in prose
    assert "rework" not in prose
    assert "reallocation" not in prose


def test_simulation_is_optional():
    without = narrate(spend=SPEND, waste=WASTE, process=PROCESS)
    with_sim = narrate(spend=SPEND, waste=WASTE, process=PROCESS, simulation=SIMULATION)
    assert "reallocation" not in _all_prose(without).lower()
    assert "reallocation" in _all_prose(with_sim).lower()
    assert len(with_sim.findings) == len(without.findings) + 1


def test_missing_sections_are_not_filled_with_zeros():
    n = narrate(process=PROCESS)
    keys = {f.key for f in n.facts}
    assert "total_cost" not in keys
    assert "top_waste" not in keys


def test_empty_collections_are_handled():
    n = narrate(spend={}, waste=[], process={}, simulation={})
    assert isinstance(n, Narrative)
    assert n.facts == []


# --- observed / modelled / inferred classification ------------------------


def test_evidence_classes_come_from_the_data():
    facts = {f.key: f for f in build_facts(spend=SPEND, waste=WASTE, process=PROCESS,
                                           simulation=SIMULATION)}
    # kind is read off the payload, not decided here
    assert facts["basis_ci"].evidence == "observed"       # metered
    assert facts["basis_labour"].evidence == "inferred"   # session inference
    assert facts["basis_ai"].evidence == "modelled"       # synthetic tokens
    # counted things
    assert facts["work_items"].evidence == "observed"
    assert facts["rework_returns"].evidence == "observed"
    # anything from the simulator
    assert facts["scenario_net_cost"].evidence == "modelled"
    assert facts["scenario_band"].evidence == "modelled"


def test_simulator_output_is_never_labelled_observed():
    for f in build_facts(simulation=SIMULATION):
        assert f.evidence == "modelled", f.key


def test_meeting_cost_is_modelled_not_observed():
    """Meeting time is an overlay driven by one assumption. Calling it
    observed would be the single most misleading label available here."""
    facts = {f.key: f for f in build_facts(waste=WASTE)}
    assert facts["top_waste"].evidence == "modelled"


def test_unpriced_latency_is_reported_as_duration_not_rupees():
    n = narrate(waste=WASTE)
    prose = _all_prose(n)
    assert "5,000 h" in prose
    assert "never priced" in prose


def test_unknown_kind_falls_back_to_inferred_not_observed():
    """An unrecognised label must degrade to the weakest claim, never the
    strongest."""
    facts = {f.key: f for f in build_facts(
        spend={"totalCost": 1.0, "byBasis": {"x": {"cost": 5.0, "kind": "who-knows"}}}
    )}
    assert facts["basis_x"].evidence == "inferred"


def test_evidence_section_omits_classes_with_no_facts():
    n = narrate(simulation=SIMULATION)  # modelled only
    assert {e.type for e in n.evidence} == {"modelled"}


# --- the product's standing promises --------------------------------------


def test_recommendation_never_names_a_person_and_says_it_is_not_a_decision():
    n = narrate(spend=SPEND, waste=WASTE, process=PROCESS, simulation=SIMULATION)
    assert "no individual is named" in n.recommendation.lower()
    assert "not a decision" in n.recommendation.lower()


def test_narrator_holds_no_session_and_makes_no_call():
    """Structural: the module must not import a DB session or an HTTP client.
    A narrator that can query can produce a number nobody checked."""
    src = (narrator.__file__ or "")
    text = open(src, encoding="utf-8").read()
    for forbidden in ("get_read_session", "get_read_engine", "sqlalchemy", "requests", "httpx"):
        assert forbidden not in text, f"narrator must not reference {forbidden}"


@pytest.mark.parametrize("bad", [None, {}, [], 0, ""])
def test_falsy_inputs_never_raise(bad):
    assert isinstance(narrate(spend=bad, waste=bad, process=bad, simulation=bad), Narrative)


# --- the endpoint ---------------------------------------------------------


def _client():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


def test_narrate_route_is_mounted_and_returns_the_contract():
    r = _client().post(
        "/narrate",
        json={"spend": SPEND, "waste": WASTE, "process": PROCESS, "simulation": SIMULATION},
    )
    assert r.status_code == 200
    body = r.json()
    assert set(body) >= {"summary", "findings", "implication", "recommendation", "evidence"}
    assert all(e["type"] in {"observed", "modelled", "inferred"} for e in body["evidence"])


def test_narrate_accepts_an_empty_body_rather_than_erroring():
    """An empty dashboard is not a client error, and a 4xx would push callers
    into rendering a failure state for a truthful 'nothing supplied'."""
    r = _client().post("/narrate", json={})
    assert r.status_code == 200
    assert r.json()["findings"] == []


def test_narrate_rejects_a_malformed_body():
    r = _client().post("/narrate", json={"spend": "not-an-object"})
    assert r.status_code == 422
