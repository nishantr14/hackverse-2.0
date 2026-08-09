"""
Review latency — recoverable idle time, not pure loss.
Owner: Diljit (waste lane).
Phase: Tier 1.

    python -m app.waste.review_latency

TWO definitions, reported separately, never blended:

  requested_to_first_response   review_requested -> first review/approval/
                                 changes_requested on the same case. Only
                                 exists where a review_requested event does.
  pr_opened_to_first_review     work_item.opened_at -> first review. Covers
                                 every PR, including the many Apache
                                 reviewers just review without a formal
                                 request for.

Only 2,358 review_requested events exist against 5,632 PRs, so the two
populations disagree by construction — blending them into one number would
hide that disagreement rather than report it. Both come from
v_review_latency_both (migrations/003), which reuses the frozen schema's
own v_review_latency for the first definition rather than re-deriving it.

Framed as recoverable idle time, not pure loss: some batching of reviews is
healthy, and overclaiming this number is the fastest way to lose the room.
"""

from __future__ import annotations

import statistics
from collections import defaultdict

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import write_session
from app.waste.common import WasteFinding

EVIDENCE_QUERY = "SELECT definition, work_item_id, latency_hours FROM v_review_latency_both"


def detect(session: Session) -> list[WasteFinding]:
    rows = session.execute(text(EVIDENCE_QUERY)).all()
    by_definition: dict[str, list[float]] = defaultdict(list)
    for definition, _work_item_id, hours in rows:
        if hours is not None and hours >= 0:
            by_definition[definition].append(float(hours))

    findings = []
    for definition, hours_list in sorted(by_definition.items()):
        median = statistics.median(hours_list)
        findings.append(
            WasteFinding(
                detector=f"review_latency:{definition}",
                hours=sum(hours_list),
                cost=None,
                unit_note=(
                    f"n={len(hours_list):,} cases, median={median:.1f}h, "
                    f"mean={statistics.mean(hours_list):.1f}h. Recoverable idle "
                    "time, not pure loss — some review batching is healthy."
                ),
                evidence_query=f"{EVIDENCE_QUERY} WHERE definition = '{definition}'",
            )
        )
    return findings


def main() -> int:
    with write_session() as session:
        findings = detect(session)
    print("\n  REVIEW LATENCY (two definitions, never blended)")
    for f in findings:
        print(f"    {f.detector:<45} {f.hours:>10,.0f}h total")
        print(f"      {f.unit_note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
