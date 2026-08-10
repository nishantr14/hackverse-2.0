"""
Counterfactual "what-if" simulator.
Owner: Dipen (models lane).
Phase: Tier 2. Hour-20 gate: must run end to end, or the whole team moves
onto it (see .claude/CLAUDE.md).

    move N engineers from one component to another
    -> how much later does the source ship, how much earlier does the
       destination ship, and what does the pair of those cost, net

DETERMINISTIC. No randomness, no LLM, no model weights. Every input is a
measured column of `v_component_capacity`; every assumption is a named
constant in this file that is returned in the response so the UI can badge
it. Run it twice with the same arguments and you get the same bytes.

THE ONE ARGUMENT THIS MAKES
    A reallocation is normally pitched with only its upside: "move five
    people onto Payments and it ships a month early." That is half a
    subtraction. This prices the project that LOSES the people too, which
    is why a scenario that looks like a win can come back as a net bill.

THE THREE ASSUMPTIONS, STATED
    1. LINEAR CAPACITY. Throughput scales with headcount. Real teams have
       coordination overhead that makes this optimistic for large moves —
       so the source's slip here is, if anything, understated.
    2. RAMP-UP. A transferred engineer does not land at full speed. They
       contribute at RAMP_EFFICIENCY for RAMP_WEEKS before reaching parity.
    3. OPEN WORK IS THE BACKLOG. "Ships" means "clears the work currently
       open on that component at its observed rate".

    None of the three is hidden: they are returned in `assumptions` on
    every response, and the UI is required to show them next to the number.

WHAT IT WILL NOT DO
    It will not move more engineers off a component than actually work
    there, and it will not forecast a component that has never closed
    anything — there is no observed rate to scale. Both refuse loudly
    rather than returning a confident-looking zero.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

# ---------------------------------------------------------------------
# Assumptions. Named, constant, and returned to the caller.
# ---------------------------------------------------------------------

#: Weeks a transferred engineer takes to reach the destination's full pace.
RAMP_WEEKS: int = 6

#: What they contribute, on average, during those weeks. 0.5 = half speed.
RAMP_EFFICIENCY: float = 0.5

#: A forecast is only as tight as the delivery it is extrapolating from.
#: Week-to-week throughput CV is multiplied by this to get the P10-P90
#: width in percentage points, then clamped to the range below.
CV_TO_SPREAD_PCT: float = 35.0
MIN_SPREAD_PCT: float = 12.0
MAX_SPREAD_PCT: float = 55.0

#: Refuse rather than divide by zero.
MIN_ITEMS_PER_WEEK: float = 1e-9


class ScenarioRefused(ValueError):
    """The scenario cannot be answered from observed data. Say why, loudly."""


@dataclass(frozen=True)
class Component:
    """One row of v_component_capacity."""

    repo: str
    component: str
    n_engineers: int
    open_items: int
    items_per_week: float
    throughput_cv: float
    cost: Decimal | None
    span_weeks: float

    @property
    def key(self) -> str:
        return f"{self.repo}/{self.component}"

    @property
    def weekly_burn(self) -> float:
        """Rupees per week this component currently consumes.

        None cost means k-anonymity suppressed it — the scenario can still
        report weeks, it just cannot price them.
        """
        if self.cost is None or self.span_weeks <= 0:
            return 0.0
        return float(self.cost) / self.span_weeks


@dataclass(frozen=True)
class Scenario:
    source: Component
    destination: Component
    engineer_count: int


@dataclass
class Result:
    source_delta_weeks: float
    dest_delta_weeks: float
    net_cost_rupees: float
    confidence_low: float
    confidence_high: float
    confidence_percent: float
    ramp_up_penalty_applied: bool
    ramp_up_note: str | None
    priced: bool
    price_note: str | None
    assumptions: dict[str, object] = field(default_factory=dict)


CAPACITY_SQL = """
SELECT repo, component, n_engineers, open_items, items_per_week,
       throughput_cv, cost, span_weeks
FROM v_component_capacity
WHERE repo = :repo AND component = :component
"""

LIST_SQL = """
SELECT repo, component, n_engineers, open_items, items_per_week,
       throughput_cv, cost, span_weeks
FROM v_component_capacity
WHERE n_engineers > 0 AND closed_items > 0
ORDER BY cost DESC NULLS LAST, n_engineers DESC
"""


def _row_to_component(row) -> Component:
    return Component(
        repo=row[0],
        component=row[1],
        n_engineers=int(row[2] or 0),
        open_items=int(row[3] or 0),
        items_per_week=float(row[4] or 0),
        throughput_cv=float(row[5] or 0),
        cost=Decimal(str(row[6])) if row[6] is not None else None,
        span_weeks=float(row[7] or 1),
    )


def list_components(session: Session, limit: int | None = None) -> list[Component]:
    """Components a scenario can actually be run on, richest first."""
    rows = session.execute(text(LIST_SQL)).all()
    out = [_row_to_component(r) for r in rows]
    return out[:limit] if limit else out


def load_component(session: Session, key: str) -> Component:
    """`key` is "repo/component", e.g. "apache/kafka/streams"."""
    repo, _, component = key.rpartition("/")
    if not repo or not component:
        raise ScenarioRefused(
            f"{key!r} is not a component key. Expected 'repo/component', "
            "e.g. 'apache/kafka/streams'."
        )
    row = session.execute(
        text(CAPACITY_SQL), {"repo": repo, "component": component}
    ).first()
    if row is None:
        raise ScenarioRefused(f"no component {key!r} in the event log")
    return _row_to_component(row)


def _weeks_to_clear(open_items: int, items_per_week: float) -> float:
    if items_per_week <= MIN_ITEMS_PER_WEEK:
        return float("inf")
    return open_items / items_per_week


def _spread_pct(source: Component, destination: Component) -> float:
    """P10-P90 width in percentage points, from observed delivery variance.

    Two independent forecasts combine in quadrature — the net figure is a
    sum of two uncertain things, not one.
    """
    combined = (source.throughput_cv**2 + destination.throughput_cv**2) ** 0.5
    return max(MIN_SPREAD_PCT, min(MAX_SPREAD_PCT, combined * CV_TO_SPREAD_PCT))


def run(scenario: Scenario) -> Result:
    """Price a reallocation. Deterministic given the scenario."""
    source, dest, n = scenario.source, scenario.destination, scenario.engineer_count

    if n <= 0:
        raise ScenarioRefused("engineer_count must be at least 1")
    if source.key == dest.key:
        raise ScenarioRefused("source and destination are the same component")
    if n >= source.n_engineers:
        raise ScenarioRefused(
            f"cannot move {n} engineers off {source.key}: only "
            f"{source.n_engineers} have ever worked on it, and moving all of "
            "them leaves no observed rate to forecast from."
        )
    for c in (source, dest):
        if c.items_per_week <= MIN_ITEMS_PER_WEEK:
            raise ScenarioRefused(
                f"{c.key} has closed nothing in the window, so it has no "
                "observed delivery rate to scale. Nothing honest to forecast."
            )

    # --- source: loses n engineers, throughput falls proportionally -----
    source_after = source.items_per_week * (source.n_engineers - n) / source.n_engineers
    source_before_weeks = _weeks_to_clear(source.open_items, source.items_per_week)
    source_after_weeks = _weeks_to_clear(source.open_items, source_after)
    source_delta = source_after_weeks - source_before_weeks

    # --- destination: gains n engineers, at ramp-up efficiency ----------
    # The penalty is real but bounded: it applies over RAMP_WEEKS, and the
    # forecast horizon is usually longer, so blend rather than apply flat.
    dest_before_weeks = _weeks_to_clear(dest.open_items, dest.items_per_week)
    horizon = max(dest_before_weeks, 1.0)
    ramp_blend = (
        RAMP_EFFICIENCY
        if horizon <= RAMP_WEEKS
        else (RAMP_EFFICIENCY * RAMP_WEEKS + (horizon - RAMP_WEEKS)) / horizon
    )
    # Reported to 2dp, not 1. At n=1 over a long horizon the blend lands
    # around 0.98, which `.1f` rendered as "gains 1.0 effective engineers,
    # not 1" — a sentence that contradicts itself and erases the only thing
    # it exists to say. The rounding was invisible at n=25 (24.5 vs 25) and
    # wrong at the size a director actually moves people in.
    effective_added = n * ramp_blend
    dest_after = (
        dest.items_per_week * (dest.n_engineers + effective_added) / dest.n_engineers
    )
    dest_after_weeks = _weeks_to_clear(dest.open_items, dest_after)
    dest_delta = dest_after_weeks - dest_before_weeks

    # --- price both halves ---------------------------------------------
    priced = source.cost is not None and dest.cost is not None
    net = source_delta * source.weekly_burn + dest_delta * dest.weekly_burn

    price_note = None
    if not priced:
        withheld = [c.key for c in (source, dest) if c.cost is None]
        price_note = (
            "spend withheld by the k-anonymity floor for "
            + ", ".join(withheld)
            + " — weeks are still observed, but this scenario cannot be priced."
        )

    spread = _spread_pct(source, dest)

    return Result(
        source_delta_weeks=round(source_delta, 2),
        dest_delta_weeks=round(dest_delta, 2),
        net_cost_rupees=round(net, 2) if priced else 0.0,
        confidence_low=round(50 - spread / 2, 1),
        confidence_high=round(50 + spread / 2, 1),
        confidence_percent=round(100 - spread, 1),
        ramp_up_penalty_applied=True,
        ramp_up_note=(
            f"{n} engineer{'s' if n > 1 else ''} "
            f"contribute{'' if n > 1 else 's'} at {RAMP_EFFICIENCY:.0%} for "
            f"{RAMP_WEEKS} weeks before reaching full pace on {dest.component} "
            f"— the destination gains {effective_added:.2f} effective "
            f"engineers, not {n}."
        ),
        priced=priced,
        price_note=price_note,
        assumptions={
            "linearCapacity": (
                "throughput scales with headcount; real coordination overhead "
                "makes this optimistic, so the source slip is understated"
            ),
            "rampUpWeeks": RAMP_WEEKS,
            "rampUpEfficiency": RAMP_EFFICIENCY,
            "backlogDefinition": "open work items on the component",
            "confidenceBasis": (
                "P10-P90 width from each component's observed week-to-week "
                "throughput variability, combined in quadrature"
            ),
            "sourceEngineers": source.n_engineers,
            "destEngineers": dest.n_engineers,
            "sourceItemsPerWeek": round(source.items_per_week, 3),
            "destItemsPerWeek": round(dest.items_per_week, 3),
            "sourceOpenItems": source.open_items,
            "destOpenItems": dest.open_items,
        },
    )
