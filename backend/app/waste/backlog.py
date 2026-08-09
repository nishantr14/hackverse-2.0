"""
Backlog time — ticket_created to first commit. Work sitting still, costing
nothing, delaying everything.
Owner: Diljit (waste lane).
Phase: Tier 1.

    python -m app.waste.backlog

The most Celonis-shaped finding in the product: no competitor computes this
because none of them join Jira to git. v_backlog_time (frozen schema)
already defines the pair; this module reports median and P90, segmented by
priority and issue_type, per the brief. No pricing — idle time has no
labour cost attached to it by construction, which is the point.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import write_session
from app.waste.common import WasteFinding

EVIDENCE_QUERY = "SELECT work_item_id, backlog_hours, priority, issue_type FROM v_backlog_time_full"


@dataclass(frozen=True)
class BacklogSegment:
    segment: str
    value: str
    n: int
    median_hours: float
    p90_hours: float


def _percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round(p * (len(ordered) - 1))))
    return ordered[idx]


def detect(session: Session) -> tuple[WasteFinding, list[BacklogSegment]]:
    rows = session.execute(text(EVIDENCE_QUERY)).all()
    hours = [float(h) for _wid, h, _pri, _typ in rows if h is not None and h >= 0]

    segments: list[BacklogSegment] = []
    for label, idx in (("priority", 2), ("issue_type", 3)):
        by_value: dict[str, list[float]] = defaultdict(list)
        for row in rows:
            value, h = row[idx], row[1]
            if value and h is not None and h >= 0:
                by_value[value].append(float(h))
        for value, vals in sorted(by_value.items(), key=lambda kv: -len(kv[1])):
            segments.append(
                BacklogSegment(
                    segment=label,
                    value=value,
                    n=len(vals),
                    median_hours=statistics.median(vals),
                    p90_hours=_percentile(vals, 0.9),
                )
            )

    finding = WasteFinding(
        detector="backlog_time",
        hours=sum(hours),
        cost=None,
        unit_note=(
            f"n={len(hours):,} cases with Jira + a first commit. "
            f"median={statistics.median(hours):.1f}h, p90={_percentile(hours, 0.9):.1f}h. "
            "Work sitting still, costing nothing, delaying everything — "
            "not priced, deliberately: idle time has no labour cost by "
            "construction."
        ),
        evidence_query=EVIDENCE_QUERY,
    )
    return finding, segments


def main() -> int:
    with write_session() as session:
        finding, segments = detect(session)
    print("\n  BACKLOG TIME")
    print(f"    {finding.unit_note}")
    print("\n    segmented:")
    for s in segments[:15]:
        print(
            f"      {s.segment:<10} {s.value:<15} n={s.n:>5,} "
            f"median={s.median_hours:>8,.1f}h p90={s.p90_hours:>8,.1f}h"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
