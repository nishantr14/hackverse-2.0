"""
Capability index — BASELINE, a weighted count and not a model.
Owner: Dipen (models lane).
Phase: Tier 2.

Answers one question for the simulator: if this actor starts working on this
component, are they on evidenced ground or new ground? That becomes an
effectiveness multiplier on their capacity at the destination — the ramp-up
penalty in P11.

    capability = 0.6 * share of merged work in the component
               + 0.4 * share of reviews given in the component

both read from v_actor_component_activity (frozen schema). Shares are
normalised against an equal split, so an actor carrying an average load of
the component scores 1.0, and the result is clamped to CLAMP so one prolific
contributor cannot hand the simulator a 6x speedup.

NO RECENCY DECAY IN THIS BASELINE. v_actor_component_activity is a lifetime
aggregate with only `last_active` to date it — a 180-day half-life (playbook
P10) needs a windowed query against event_log, which is the upgrade path, not
this. Stated in `basis` rather than quietly approximated.

PRIVACY — the reason this file is not a router:
  - It is per-actor by construction, so it MUST NEVER be exposed through any
    API route at any aggregation (CLAUDE.md: no per-person view for cost,
    capability or AI usage). Two consumers only: the simulator's ramp-up
    factor, and the key-person detector.
  - v_actor_component_activity is deliberately NOT granted to the esi_app
    read-only role (docs/schema.sql, section 7). Pass a write-role session
    here; a read session will fail on permissions, which is the schema
    enforcing the rule rather than this docstring asking nicely.
  - Capability carries no name or login — actor_hash in, a float out. The
    return type has no identity field at all, so a caller cannot leak one.

`get_capability(...)` is the interface P11 calls. When a real capability
model lands it replaces the body and changes `basis`; the signature and the
Capability shape stay put.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

MERGE_WEIGHT = 0.6
REVIEW_WEIGHT = 0.4

#: An actor with no evidenced history in a component is not assumed useless —
#: they are assumed average, and the simulator applies its own ramp-up factor
#: on top. Guessing downward here would double-count the penalty.
NEUTRAL = 1.0

#: Effectiveness floor/ceiling. Evidence this coarse cannot justify claiming
#: someone is a tenth as effective, or triple.
CLAMP = (0.5, 1.5)

_COMPONENT_ACTIVITY_QUERY = """
    SELECT actor_hash, n_merged, n_reviews
      FROM v_actor_component_activity
     WHERE component = :component
"""


@dataclass(frozen=True)
class Capability:
    """No actor field, by design — see the privacy note in the module docstring."""

    effectiveness: float
    #: 'historical_component_throughput' | 'neutral_fallback'
    basis: str
    #: Actors with evidence in this component. Lets the caller see how thin
    #: the ground under the number is.
    n_actors_in_component: int = 0


def _clamp(x: float) -> float:
    return max(CLAMP[0], min(CLAMP[1], x))


def score(
    actor_merged: int,
    actor_reviews: int,
    total_merged: int,
    total_reviews: int,
    n_actors: int,
) -> float:
    """The weighted-share arithmetic, normalised so average work scores 1.0.

    Pure, so it is testable with no database. Each share is measured against
    an equal split (1/n_actors); an actor doing exactly their share of both
    scores exactly 1.0.
    """
    if n_actors <= 0:
        return NEUTRAL
    equal_share = 1.0 / n_actors
    merge_share = (actor_merged / total_merged) if total_merged else equal_share
    review_share = (actor_reviews / total_reviews) if total_reviews else equal_share
    weighted = MERGE_WEIGHT * merge_share + REVIEW_WEIGHT * review_share
    return _clamp(weighted / equal_share)


def get_capability(session: Session, actor_hash: str, component: str) -> Capability:
    """Effectiveness multiplier for this actor on this component.

    Falls back to NEUTRAL with basis='neutral_fallback' when the component has
    no history, or this actor none in it. This is the interface P11 depends
    on — keep the signature stable.
    """
    rows = list(session.execute(text(_COMPONENT_ACTIVITY_QUERY), {"component": component}))
    if not rows:
        return Capability(NEUTRAL, "neutral_fallback", 0)

    total_merged = sum(int(r.n_merged) for r in rows)
    total_reviews = sum(int(r.n_reviews) for r in rows)
    mine = next((r for r in rows if r.actor_hash == actor_hash), None)
    if mine is None or (int(mine.n_merged) == 0 and int(mine.n_reviews) == 0):
        return Capability(NEUTRAL, "neutral_fallback", len(rows))

    return Capability(
        effectiveness=score(
            int(mine.n_merged), int(mine.n_reviews), total_merged, total_reviews, len(rows)
        ),
        basis="historical_component_throughput",
        n_actors_in_component=len(rows),
    )
