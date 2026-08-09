"""
Narrate router — turns figures that already exist into an explanation.
Owner: Livana (narration).
Phase: Tier 2.

It computes nothing. The request carries figures the other routers already
produced, and the response is prose over exactly those figures — see
app/narrate/narrator.py for why the evidence class of each one is read from
the data rather than assigned here.

NO DATABASE SESSION, DELIBERATELY. Every other router takes one; this must
not. A narrator holding a session could query, and a narrator that can query
can produce a number nobody checked. Passing the figures in is what makes
"the AI explains numbers, it never computes one" a property of the wiring
rather than a promise in a docstring.

The request is permissive on purpose: every section is optional, and a
missing one produces no sentences about it rather than a zero.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.narrate.narrator import Narrative, narrate

router = APIRouter(prefix="/narrate", tags=["narrate"])


class NarrateRequest(BaseModel):
    """Analytics to explain. Shapes mirror what /spend/summary,
    /waste/by-project, /process/map and /simulate already return, so a caller
    forwards those responses unchanged rather than reshaping them."""

    spend: dict[str, Any] | None = None
    waste: list[dict[str, Any]] | None = None
    process: dict[str, Any] | None = None
    simulation: dict[str, Any] | None = Field(
        default=None,
        description="A /simulate response. Optional — everything it produces is "
        "labelled modelled, and omitting it simply removes those sentences.",
    )

    model_config = {"populate_by_name": True}


@router.post("", response_model=Narrative)
def post_narrate(req: NarrateRequest) -> Narrative:
    """Explain the supplied figures.

    Always 200, including for an empty body: "nothing was supplied" is a
    truthful narrative and a 4xx would push the caller into treating an empty
    dashboard as an error.
    """
    return narrate(
        spend=req.spend,
        waste=req.waste,
        process=req.process,
        simulation=req.simulation,
    )
