"""
Shared types and helpers for the waste detectors.
Owner: Diljit (waste lane).
Phase: Tier 1.

Every detector returns a WasteFinding: hours, cost, a unit_note, and the
SQL that produced it (evidence_query) — the UI reveals the query on demand,
which is the cheapest possible answer to "where did that number come from".

Hours are always computed from real activity. Cost is None, with a reason
in `cost_pending`, whenever pricing needs a citation config/rates.yaml
doesn't have yet — never a guess, never zero standing in for "unknown".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from app.cost.rate_card import RATES_PATH, RateCardError, is_placeholder


@dataclass(frozen=True)
class WasteFinding:
    detector: str
    hours: float | None
    cost: float | None
    unit_note: str
    evidence_query: str
    #: Set when cost is None because a citation is missing. Never set
    #: because the underlying activity itself is absent — that's hours=0.
    cost_pending: str | None = None


def citation_for(
    cfg: dict[str, Any],
    section: str,
    fields: tuple[str, ...] = ("publisher", "url", "retrieved"),
) -> str:
    """Same fail-closed contract as rate_card.citation, generalised to any
    cited config/rates.yaml section (ci_cost, carbon, ...)."""
    block = cfg.get(section) or {}
    source = block.get("source") or {}
    missing = [
        f
        for f in fields
        if not str(source.get(f) or "").strip() or is_placeholder(str(source.get(f)))
    ]
    if missing:
        raise RateCardError(
            f"{section}.source is incomplete: {', '.join(missing)} not set in "
            f"{RATES_PATH}.\nThe citation renders on screen beside the money. "
            "An uncited figure is an invented one — fill these in before pricing."
        )
    retrieved = str(source["retrieved"]).strip()
    try:
        date.fromisoformat(retrieved)
    except ValueError as exc:
        raise RateCardError(
            f"{section}.source.retrieved must be YYYY-MM-DD, got {retrieved!r}"
        ) from exc
    return f"{source['publisher']} — {source['url']} (retrieved {retrieved})"
