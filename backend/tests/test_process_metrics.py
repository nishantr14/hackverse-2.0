"""
The Process screen's headline figures come from the log, never from the map.

WHY THIS FILE EXISTS AT ALL, AND WHY IT LIVES IN THE BACKEND SUITE

`/process/map` deliberately returns an INCOMPLETE edge list: the top `limit`
transitions by cost, because 168 edges drawn at once is a hairball in which
nothing is legible and therefore nothing is true. That filter is right for a
picture and wrong for a measurement. Twice now a metric has been summed across
those filtered edges and contradicted itself on screen:

  - "Returns to review" counted inbound edges to `changes_requested`. Every one
    of them falls below the cost cut, so it printed 0 directly beneath a
    headline saying 6% of work takes the rework loop, while the log held 440.
  - "Work items" summed the transitions leaving `commit`: 1,902 against a real
    4,949, so the variant shares never reconciled against it.
  - The per-variant legend summed `costRupees` and `frequency` the same way,
    one aggregate further down, and understated the rework loop.

The defect is not arithmetic, it is a category error — deriving a total from a
set that was truncated for legibility — so an assertion on a number would not
catch the next instance of it. The static half of this file asserts the SHAPE
of the frontend's metric functions instead: that they do not read `graph.edges`
at all. That is checkable without a browser, without a test runner the frontend
does not have, and it fails the moment somebody reaches for the filtered list
again.

The frontend has no test dependency and adding one is not this file's call.
Parsing its source from here is the cheap half of that trade, and it holds the
property that actually matters.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

REPO_ROOT = Path(__file__).resolve().parents[2]
PROCESS_LIB = REPO_ROOT / "frontend" / "src" / "lib" / "process.ts"

#: Functions whose output is a MEASUREMENT rendered as a headline figure, as
#: opposed to geometry. Anything here must be independent of the cost filter.
METRIC_FUNCTIONS = ("reworkPasses", "totalWorkItems")


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def _function_body(source: str, name: str) -> str:
    """The text of `export function <name>(...) { ... }`, braces matched.

    Brace matching rather than a regex: a regex that stops at the first `}`
    would clip any function containing an object literal or a nested arrow,
    and a check that silently reads half a function is worse than no check.
    """
    match = re.search(rf"export function {name}\s*\(", source)
    assert match, f"{name} is gone from process.ts — this test needs updating"
    start = source.index("{", match.end())
    depth = 0
    for i in range(start, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start : i + 1]
    raise AssertionError(f"unbalanced braces in {name}")


def _strip_comments(text_: str) -> str:
    """These functions explain at length what they must not do. A substring
    check over the prose would pass on the explanation and fail on nothing."""
    text_ = re.sub(r"/\*.*?\*/", "", text_, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", "", text_)


# --- the static guard ------------------------------------------------------


def test_the_headline_metrics_never_read_the_drawn_edge_list():
    source = PROCESS_LIB.read_text(encoding="utf-8")
    for name in METRIC_FUNCTIONS:
        body = _strip_comments(_function_body(source, name))
        assert ".edges" not in body, (
            f"{name}() reads the drawn edge list, which /process/map filters to "
            "the costliest transitions. Take the figure from the API instead."
        )


def test_the_per_variant_totals_come_from_the_summary_not_the_edges():
    """`variantStats` is allowed to slice `graph.edges` — it has to, to draw
    each variant — so the guard here is on the two fields that are FIGURES."""
    body = _strip_comments(_function_body(PROCESS_LIB.read_text(encoding="utf-8"), "variantStats"))
    assert "cost: v.totalCost" in body, "per-variant cost must come from variantSummary"
    assert "cases: v.nCases" in body, "per-variant case count must come from variantSummary"
    assert "reduce" not in body, (
        "variantStats sums something across the filtered edges again — every "
        "figure it returns has to come off variantSummary"
    )


def test_an_absent_measurement_is_null_rather_than_zero():
    """A backend that did not send the figure and a backend that measured zero
    are different facts, and the screen prints them differently."""
    body = _function_body(PROCESS_LIB.read_text(encoding="utf-8"), "reworkPasses")
    assert "?? null" in body, "reworkPasses must fall back to null, never to 0"


# --- the contract the guard depends on -------------------------------------


def test_the_map_reports_returns_to_review_as_its_own_measurement(client, pg_engine):
    body = client.get("/process/map").json()
    assert "reworkReturns" in body, (
        "the frontend cannot stop counting off the graph unless the API sends "
        "this — putting the metric back on the edges is the only alternative"
    )
    rework = body["reworkReturns"]
    assert set(rework) == {"events", "cases"}
    assert rework["events"] > 0 and rework["cases"] > 0


def test_returns_to_review_reconcile_with_the_rework_variant(client, pg_engine):
    """The two figures on the screen are the same fact stated twice.

    Cases that were sent back for changes ARE the cases the classifier calls
    `rework_loop`, so the counts have to agree. This is the equality whose
    absence produced "6% take the rework loop" over "returns to review: 0".

    `triple_review` is deliberately NOT in this sum. It is classified on the
    number of review rounds, not on a `changes_requested` event — 1,360 cases
    against 299 that were ever sent back — so folding it in would make the two
    figures disagree by construction and the test would be pinning nothing.
    """
    body = client.get("/process/map").json()
    rework_cases = body["reworkReturns"]["cases"]
    classified = next(
        v["nCases"] for v in body["variantSummary"] if v["variant"] == "rework_loop"
    )
    assert rework_cases == classified, (
        f"{rework_cases} cases were sent back for changes but the classifier "
        f"put {classified} on the rework loop"
    )
    # At least one return per case, and more than one wherever a case went
    # round twice — the reason `events` and `cases` are reported separately.
    assert body["reworkReturns"]["events"] >= rework_cases


def test_the_work_item_total_is_bigger_than_the_drawn_graph_can_account_for(
    client, pg_engine
):
    """The regression this file is named after, stated as a property.

    Summing the transitions leaving `commit` gave 1,902 against a real 4,949.
    Asserting the two DISAGREE is what pins it: if they ever match, the cost
    filter has stopped filtering and the guard above has stopped meaning
    anything.
    """
    body = client.get("/process/map").json()
    from_summary = sum(v["nCases"] for v in body["variantSummary"])
    off_the_graph = sum(e["frequency"] for e in body["edges"] if e["from"] == "commit")
    assert from_summary > 0
    assert body["coverage"]["transitionsShown"] < body["coverage"]["transitionsTotal"], (
        "the map is no longer filtered, so this whole class of bug is moot — "
        "delete this file rather than weakening it"
    )
    assert off_the_graph != from_summary, (
        "counting work items off the drawn edges now agrees with the real "
        "total; if that is genuine the filter is gone, and if it is not, "
        "something is summing the wrong set again"
    )
