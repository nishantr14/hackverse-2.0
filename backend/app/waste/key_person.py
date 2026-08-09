"""
Key-person exposure — the component is reported, never the actor.
Owner: Diljit (waste lane).
Phase: Tier 2.

    python -m app.waste.key_person

Max share of a component's activity (merges + reviews) held by one actor.
Reads v_actor_component_activity — INTERNAL ONLY per the frozen schema's
own comment, per-actor by construction, never granted to the app role and
never exposed through a route. This module is the one permitted reader: it
computes a per-actor ratio in Python and returns only the component and the
share, never the actor_hash that produced it.

k-anonymity enforced here, in Python, because this source view has none
built in (it exists specifically so something downstream can add it). A
component with fewer contributors than K_ANONYMITY_FLOOR is suppressed —
returned with the flag set and no share, same contract as the SQL-side
suppression in v_spend_by_component, never silently dropped.

Framed as "the organisation is not developing anyone else here" rather than
naming who holds the concentration — same computation, better story, and
the only framing the privacy rules permit anyway.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings

EVIDENCE_QUERY = """
    SELECT component, actor_hash, n_merged + n_reviews AS activity
      FROM v_actor_component_activity
     WHERE component IS NOT NULL
"""


@dataclass(frozen=True)
class KeyPersonExposure:
    component: str
    n_actors: int
    max_share: float | None  # None when suppressed
    suppressed: bool
    k_applied: int


def detect(session: Session) -> list[KeyPersonExposure]:
    floor = get_settings().k_anonymity_floor
    rows = session.execute(text(EVIDENCE_QUERY)).all()

    # v_actor_component_activity groups by every event that touched the
    # component, so an actor who only ever committed there (never merged or
    # reviewed) shows up with activity=0 — real, but not what "concentration
    # of merges and reviews" means. Counted out here, not just weighted to
    # zero, so n_actors reflects who actually had a say.
    by_component: dict[str, list[int]] = defaultdict(list)
    for component, _actor_hash, activity in rows:
        if int(activity) > 0:
            by_component[component].append(int(activity))

    results = []
    for component, activities in by_component.items():
        n_actors = len(activities)
        suppressed = n_actors < floor
        total = sum(activities)
        share = (max(activities) / total) if (not suppressed and total > 0) else None
        results.append(
            KeyPersonExposure(
                component=component,
                n_actors=n_actors,
                max_share=share,
                suppressed=suppressed,
                k_applied=floor,
            )
        )
    return sorted(
        results, key=lambda r: (r.max_share is None, -(r.max_share or 0))
    )


def main() -> int:
    from app.db.session import write_session

    with write_session() as session:
        results = detect(session)
    print("\n  KEY-PERSON EXPOSURE (component only, never the actor)")
    for r in results[:15]:
        if r.suppressed:
            print(f"    {r.component:<20} suppressed (k={r.k_applied}, n_actors={r.n_actors})")
        else:
            print(f"    {r.component:<20} max_share={r.max_share:.0%}  n_actors={r.n_actors}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
