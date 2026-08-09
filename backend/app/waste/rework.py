"""
Rework — changes_requested followed by a redo commit, priced honestly small.
Owner: Diljit (waste lane).
Phase: Tier 1.

    python -m app.waste.rework

v_rework_pairs (frozen schema) already defines the pair: a `commit` whose
timestamp immediately follows a `changes_requested` on the same case, via
v_transitions. Only 440 changes_requested events exist in this database —
this will be a modest figure, and it is reported as one rather than the
definition being stretched to inflate it.

Priced as gap_hours (changes_requested -> the redo commit) times the band
rate of whoever redid the work — a proxy for the cycle's cost, not a
session-inferred figure of the redo work itself. Needs rate_card seeded;
fails closed with a reason, same as every other cited figure here.

Priced through v_rework_cost (migrations/003), a single aggregate row, not
a direct join against actor here — the app role reads views only, and
actor must never be read at row grain by anything the API can reach.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.waste.common import WasteFinding

EVIDENCE_QUERY = "SELECT n_pairs, total_hours, total_cost FROM v_rework_cost"


def total_hours(session: Session) -> tuple[float, int]:
    row = session.execute(text(EVIDENCE_QUERY)).one()
    return float(row[1]), int(row[0])


def price(session: Session) -> tuple[float | None, str | None]:
    n_pairs, _hours, total_cost, n_unpriced = session.execute(
        text("SELECT n_pairs, total_hours, total_cost, n_unpriced FROM v_rework_cost")
    ).one()
    if total_cost is None:
        if n_pairs == 0:
            return 0.0, None
        return None, "rate_card is empty — seed it: python -m app.cost.rate_card --seed"
    if n_unpriced:
        return (
            float(total_cost),
            f"{n_unpriced} of {n_pairs} pair(s) have no matching rate_card "
            "band — total_cost excludes them",
        )
    return float(total_cost), None


def detect(session: Session) -> WasteFinding:
    hours, n_pairs = total_hours(session)
    cost, reason = price(session)
    return WasteFinding(
        detector="rework",
        hours=hours,
        cost=cost,
        unit_note=(
            f"n={n_pairs:,} changes_requested -> redo-commit pairs. Priced as "
            "gap_hours (a proxy for the cycle, not a session-inferred figure "
            "of the redo itself) x the redoer's band rate."
        ),
        evidence_query=EVIDENCE_QUERY,
        cost_pending=reason,
    )


def main() -> int:
    from app.db.session import write_session

    with write_session() as session:
        finding = detect(session)
    print("\n  REWORK")
    print(f"    hours   {finding.hours:,.1f}")
    print(f"    cost    {finding.cost if finding.cost is not None else 'pending: ' + (finding.cost_pending or '')}")
    print(f"    note    {finding.unit_note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
