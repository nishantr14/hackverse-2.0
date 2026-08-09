"""
Process discovery — the cost-weighted directly-follows graph.
Owner: Diljit (waste lane).
Phase: Tier 0 (feeds ProcessView, the screen that leads the Round 2 demo).

    python -m app.waste.discovery

CI_RUN IS EXCLUDED FROM EVERY SEQUENCE HERE. Measured: 7,464 cases / 3,224
distinct raw activity sequences even with ci_run excluded — not the
"every case is unique" wall the brief said to watch for, but a real
hairball without the collapse-and-significance-filter steps below.

Nodes are collapsed runs of one activity (v_collapsed_sequence — a PR with
six review rounds is one "review x6" node, not six). Edges come from
v_transitions_human / v_edges (migrations/003_process_and_waste_views.sql).

EDGE WEIGHT IS COST, NOT FREQUENCY. `case_cost_exposure` sums each
distinct case's total cost once per case touching the edge. Right now
v_case_cost only has AI-token cost to sum — real, not zero, but partial
until P5 seeds rate_card and CI pricing; it fills in with no code change
here once those land.

Significance filtering: keep edges covering the top N% of cost (or
frequency, while cost is still partial), N from config — Kafka is a
hairball unfiltered, and the filter has to exist before Livana asks why
the graph is unreadable.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

#: Edges covering this share of the ranking weight's cumulative total
#: survive the significance filter. The rest are real transitions, just not
#: ones worth spending graph space on.
SIGNIFICANCE_THRESHOLD_PCT = 90.0

EDGES_QUERY = """
    SELECT repo, source_activity, target_activity, n_transitions, n_cases,
           median_gap_hours, case_cost_exposure
      FROM v_edges
"""


@dataclass(frozen=True)
class Edge:
    repo: str
    source_activity: str
    target_activity: str
    n_transitions: int
    n_cases: int
    median_gap_hours: float | None
    cost_exposure: float
    significant: bool


def _rank_weight(edge_row, cost_available: bool) -> float:
    return float(edge_row[6]) if cost_available else float(edge_row[3])


def load_edges(session: Session, repo: str | None = None) -> list[Edge]:
    """All edges for a repo, cost-ranked when any cost exists, frequency
    otherwise (so the graph is never empty just because P5 hasn't run yet),
    with the top-N%-of-weight significance filter applied.
    """
    query = EDGES_QUERY + (" WHERE repo = :repo" if repo else "")
    rows = session.execute(text(query), {"repo": repo} if repo else {}).all()
    if not rows:
        return []

    cost_available = any(float(r[6]) > 0 for r in rows)
    weight = lambda r: _rank_weight(r, cost_available)  # noqa: E731
    ordered = sorted(rows, key=weight, reverse=True)
    total = sum(weight(r) for r in ordered) or 1.0

    edges: list[Edge] = []
    running = 0.0
    for row in ordered:
        running += weight(row)
        edges.append(
            Edge(
                repo=row[0],
                source_activity=row[1],
                target_activity=row[2],
                n_transitions=int(row[3]),
                n_cases=int(row[4]),
                median_gap_hours=float(row[5]) if row[5] is not None else None,
                cost_exposure=float(row[6]),
                significant=(100.0 * running / total) <= SIGNIFICANCE_THRESHOLD_PCT,
            )
        )
    return edges


def main() -> int:
    from app.db.session import write_session

    with write_session() as session:
        edges = load_edges(session)
    sig = [e for e in edges if e.significant]
    print(f"\n  PROCESS GRAPH — {len(edges)} edges, {len(sig)} above the significance filter")
    ranked_by_cost = any(e.cost_exposure > 0 for e in edges)
    print(f"    ranked by: {'cost exposure' if ranked_by_cost else 'frequency (no cost landed yet)'}")
    for e in sig[:10]:
        print(
            f"    {e.repo:<15} {e.source_activity:<18} -> {e.target_activity:<18} "
            f"n={e.n_transitions:>6,} cost={e.cost_exposure:>12,.0f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
