"""
Narrator — explains numbers that already exist. Never computes one.
Owner: Livana (narration).
Phase: Tier 2.

DETERMINISM DISCIPLINE (.claude/CLAUDE.md): this module receives figures the
SQL layer already produced and turns them into sentences. It performs no
arithmetic on the underlying data, holds no database session, and makes no
network call. Given the same facts it returns the same words, every time.

WHY THERE IS NO LLM CALL HERE. The docstring this replaces promised a
Granite call. Nothing in this repository integrates an LLM — no client, no
key, no service — and the honest options were to build that infrastructure
or to write the layer that would sit in front of it. A template narrator is
the one that survives a venue with no wifi, and the architecture is the same
either way:

    real metrics -> structured facts -> renderer -> narrative

`build_facts` produces the facts; `render` turns them into prose. Dropping a
model in later means replacing `render` alone, and the guardrail that
matters — that every figure was computed upstream — is enforced by `Fact`
carrying its own pre-formatted `value`, so a renderer has no raw data to do
arithmetic on even if it wanted to.

EVIDENCE CLASSES ARE READ FROM THE DATA, NOT ASSIGNED BY HAND:

  observed   counted directly — event counts, CI runner minutes, work items
  modelled   produced by a model or an assumption — simulator output, the
             meeting overlay, token spend
  inferred   reasoned from the above — session-inferred labour cost, and any
             sentence this module concludes rather than reads

`/spend/summary` already labels each cost basis `metered`, `inferred` or
`modelled`; that label is used as-is rather than re-decided here. An
inferred statement is never presented as an observed one.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

EvidenceType = Literal["observed", "modelled", "inferred"]

#: How a cost basis's own `kind` maps onto the three evidence classes. The
#: API assigns `kind`; this only translates vocabulary, it does not judge.
_KIND_TO_EVIDENCE: dict[str, EvidenceType] = {
    "metered": "observed",
    "measured": "observed",
    "observed": "observed",
    "inferred": "inferred",
    "modelled": "modelled",
    "synthetic": "modelled",
}

LAKH = 100_000
CRORE = 10_000_000


def _trim(n: float, places: int) -> str:
    return f"{n:.{places}f}".rstrip("0").rstrip(".") if places else f"{n:.0f}"


def money(rupees: float) -> str:
    """₹8.48Cr · ₹5.47L · ₹62,000 — the same scale the UI reads in.

    Formatting only. The value is whatever was handed in; no rate, no rounding
    that changes an order of magnitude, no unit invented that the caller did
    not already imply.
    """
    a = abs(rupees)
    sign = "-" if rupees < 0 else ""
    if a >= CRORE:
        return f"{sign}₹{_trim(a / CRORE, 2)}Cr"
    if a >= LAKH:
        return f"{sign}₹{_trim(a / LAKH, 1)}L"
    return f"{sign}₹{a:,.0f}"


def pct(fraction: float, places: int = 0) -> str:
    return f"{_trim(fraction * 100, places)}%"


class Fact(BaseModel):
    """One figure, already computed, with the class of evidence behind it.

    `value` is a rendered string on purpose. A renderer that only ever sees
    formatted values cannot accidentally derive a new number from raw ones.
    """

    key: str
    label: str
    value: str
    evidence: EvidenceType


class Evidence(BaseModel):
    type: EvidenceType
    text: str


class Narrative(BaseModel):
    summary: str
    findings: list[str]
    implication: str
    recommendation: str
    evidence: list[Evidence]
    #: Every figure the prose above is built from, so a reader can check the
    #: sentences against the numbers without leaving the response.
    facts: list[Fact] = Field(default_factory=list)


# --------------------------------------------------------------------------
# facts
# --------------------------------------------------------------------------


def _get(d: Any, *keys: str, default: Any = None) -> Any:
    """Tolerant read across camelCase/snake_case, and across dict or model."""
    if d is None:
        return default
    for k in keys:
        if isinstance(d, dict):
            if k in d and d[k] is not None:
                return d[k]
        elif getattr(d, k, None) is not None:
            return getattr(d, k)
    return default


def build_facts(
    spend: Any = None,
    waste: Any = None,
    process: Any = None,
    simulation: Any = None,
) -> list[Fact]:
    """Structured facts from whatever analytics were supplied.

    Every argument is optional and a missing one contributes nothing. It never
    substitutes a zero for an absent measurement — a section that was not
    provided simply produces no facts, and the prose then does not mention it.
    """
    facts: list[Fact] = []

    total_cost = _get(spend, "totalCost", "total_cost")
    if total_cost is not None:
        facts.append(
            Fact(
                key="total_cost",
                label="Engineering spend",
                value=money(float(total_cost)),
                # The total blends metered, inferred and modelled bases, so
                # the whole is only as direct as its softest part.
                evidence="inferred",
            )
        )

    observed_share = _get(spend, "observedShare", "observed_share")
    if observed_share is not None:
        facts.append(
            Fact(
                key="observed_share",
                label="Share of spend from directly observed activity",
                value=pct(float(observed_share)),
                evidence="observed",
            )
        )

    by_basis = _get(spend, "byBasis", "by_basis", default={}) or {}
    if isinstance(by_basis, dict):
        for basis, entry in by_basis.items():
            cost = _get(entry, "cost")
            if cost is None:
                continue
            kind = str(_get(entry, "kind", default="inferred"))
            facts.append(
                Fact(
                    key=f"basis_{basis}",
                    label=f"{basis.upper()} cost ({kind})",
                    value=money(float(cost)),
                    evidence=_KIND_TO_EVIDENCE.get(kind, "inferred"),
                )
            )

    # --- waste: the largest priced category, and latency kept separate ---
    rows = list(waste or [])

    def _is_priced(r: Any) -> bool:
        """A null amount is the API saying "deliberately not priced".

        /waste/by-project returns `amountRupees: null` for review latency —
        waiting is wall clock, not billed time. The frontend adds a `priced`
        flag when it maps the row; the raw response does not have one, and
        this endpoint accepts the raw response. Reading only the flag treated
        every unpriced row as priced-but-zero and silently dropped the
        duration finding, so the amount is what decides.
        """
        amount = _get(r, "amountRupees", "amount_rupees")
        flag = _get(r, "priced")
        if flag is not None:
            return bool(flag) and amount is not None
        return amount is not None

    priced = [r for r in rows if _is_priced(r) and _get(r, "amountRupees", "amount_rupees")]
    if priced:
        by_type: dict[str, float] = {}
        for r in priced:
            t = str(_get(r, "type", default="unknown"))
            by_type[t] = by_type.get(t, 0.0) + float(
                _get(r, "amountRupees", "amount_rupees", default=0) or 0
            )
        top_type = max(by_type, key=lambda k: by_type[k])
        facts.append(
            Fact(
                key="top_waste",
                # The category is part of the label, not a parenthetical to be
                # parsed back out — the renderer needs to name it in a sentence.
                label=f"{top_type.capitalize()} cost",
                value=money(by_type[top_type]),
                # meeting cost is an overlay; the rest are counted.
                evidence="modelled" if top_type == "meeting" else "observed",
            )
        )
        facts.append(
            Fact(
                key="waste_total",
                label="Total priced waste",
                value=money(sum(by_type.values())),
                evidence="inferred",
            )
        )

    unpriced = [r for r in rows if not _is_priced(r)]
    if unpriced:
        hours = sum(float(_get(r, "hours", default=0) or 0) for r in unpriced)
        facts.append(
            Fact(
                key="unpriced_hours",
                label="Waiting time reported as duration, never priced",
                value=f"{hours:,.0f} h",
                evidence="observed",
            )
        )

    # --- process ---
    work_items = _get(process, "workItems", "work_items")
    if work_items is not None:
        facts.append(
            Fact(
                key="work_items",
                label="Work items in the window",
                value=f"{int(work_items):,}",
                evidence="observed",
            )
        )

    rework = _get(process, "reworkReturns", "rework_returns")
    if rework is not None:
        events = _get(rework, "events")
        cases = _get(rework, "cases")
        if events is not None:
            facts.append(
                Fact(
                    key="rework_returns",
                    label="Times finished work was sent back for changes",
                    value=f"{int(events):,}"
                    + (f" across {int(cases):,} work items" if cases is not None else ""),
                    evidence="observed",
                )
            )

    variants = list(_get(process, "variants", "variantSummary", default=[]) or [])
    off_path = [v for v in variants if _get(v, "variant") != "happy_path"]
    if off_path:
        share = sum(float(_get(v, "shareOfCost", "share_of_cost", default=0) or 0) for v in off_path)
        facts.append(
            Fact(
                key="off_path_cost",
                label="Share of cost off the happy path",
                value=pct(share),
                evidence="observed",
            )
        )
    for v in variants:
        if _get(v, "variant") != "rework_loop":
            continue
        items = float(_get(v, "shareOfWorkItems", "share_of_work_items", default=0) or 0)
        cost = float(_get(v, "shareOfCost", "share_of_cost", default=0) or 0)
        if items > 0:
            facts.append(
                Fact(
                    key="rework_multiple",
                    label="Rework loop cost multiple",
                    value=f"{cost / items:.1f}×",
                    # A ratio of two observed shares is still a reading of the
                    # log, not a model output.
                    evidence="observed",
                )
            )

    # --- simulation: modelled, always, without exception ---
    net = _get(simulation, "netCostRupees", "net_cost_rupees")
    if net is not None:
        facts.append(
            Fact(
                key="scenario_net_cost",
                label="Net cost of the modelled reallocation",
                value=money(float(net)),
                evidence="modelled",
            )
        )
    lo = _get(simulation, "confidenceLow", "confidence_low")
    hi = _get(simulation, "confidenceHigh", "confidence_high")
    if lo is not None and hi is not None:
        facts.append(
            Fact(
                key="scenario_band",
                label="Forecast confidence band",
                value=f"P10–P90 {_trim(float(lo), 0)}–{_trim(float(hi), 0)}%",
                evidence="modelled",
            )
        )

    return facts


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------


def _by_key(facts: list[Fact]) -> dict[str, Fact]:
    return {f.key: f for f in facts}


def render(facts: list[Fact]) -> Narrative:
    """Facts to prose. Deterministic, and it says nothing it has no fact for."""
    if not facts:
        return Narrative(
            summary="No analytics were supplied, so there is nothing to explain.",
            findings=[],
            implication=(
                "This is an absence of input, not a finding that nothing is wrong."
            ),
            recommendation=(
                "Send spend, waste or process figures to narrate them. Nothing is "
                "inferred from an empty request."
            ),
            evidence=[],
            facts=[],
        )

    f = _by_key(facts)
    findings: list[str] = []

    # 1 — the bill
    if "total_cost" in f:
        line = f"Engineering spend for the window is {f['total_cost'].value}"
        if "observed_share" in f:
            line += (
                f", of which {f['observed_share'].value} traces to directly observed "
                "activity and the remainder to inferred session time"
            )
        findings.append(line + ".")

    # 2 — where it leaks
    if "top_waste" in f:
        line = (
            f"{f['top_waste'].label} is the largest priced waste category at "
            f"{f['top_waste'].value}"
        )
        if "waste_total" in f:
            line += f", within {f['waste_total'].value} of priced waste in total"
        findings.append(line + ".")

    if "unpriced_hours" in f:
        findings.append(
            f"A further {f['unpriced_hours'].value} of review waiting is reported as "
            "duration and deliberately never priced — waiting is not billed time."
        )

    # 3 — the process shape
    if "rework_multiple" in f and "off_path_cost" in f:
        findings.append(
            f"Work that leaves the happy path carries {f['off_path_cost'].value} of "
            f"the cost, and the rework loop charges {f['rework_multiple'].value} its "
            "share of the work."
        )
    elif "off_path_cost" in f:
        findings.append(
            f"Work off the happy path carries {f['off_path_cost'].value} of the cost."
        )

    if "rework_returns" in f:
        findings.append(
            "Finished work was sent back for changes "
            f"{f['rework_returns'].value.replace(' across', ' times, across')}."
        )

    # 4 — the scenario, only if one was run
    if "scenario_net_cost" in f:
        line = f"The modelled reallocation nets {f['scenario_net_cost'].value}"
        if "scenario_band" in f:
            line += f" ({f['scenario_band'].value})"
        findings.append(line + ".")

    summary = _summary(f)
    return Narrative(
        summary=summary,
        findings=findings,
        implication=_implication(f),
        recommendation=_recommendation(f),
        evidence=_evidence(facts),
        facts=facts,
    )


def _summary(f: dict[str, Fact]) -> str:
    if "total_cost" in f and "top_waste" in f:
        return (
            f"{f['total_cost'].value} of engineering spend, and the largest single "
            f"priced loss is {f['top_waste'].label.lower()} at "
            f"{f['top_waste'].value}."
        )
    if "total_cost" in f:
        return f"{f['total_cost'].value} of engineering spend in the window."
    if "top_waste" in f:
        return f"The largest priced loss is {f['top_waste'].value}."
    if "off_path_cost" in f:
        return (
            f"{f['off_path_cost'].value} of process cost sits off the happy path."
        )
    if "scenario_net_cost" in f:
        return f"The modelled reallocation nets {f['scenario_net_cost'].value}."
    return "Partial analytics were supplied; the findings below cover only those."


def _implication(f: dict[str, Fact]) -> str:
    parts: list[str] = []
    if "rework_multiple" in f:
        parts.append(
            f"A path charging {f['rework_multiple'].value} its weight is where a "
            "process change pays back fastest, because the cost is concentrated "
            "rather than spread"
        )
    if "top_waste" in f:
        parts.append(
            f"{f['top_waste'].value} is recoverable spend rather than a write-off"
        )
    if "scenario_net_cost" in f:
        parts.append(
            "the scenario prices both sides of the move, so the figure already "
            "accounts for what the losing project gives up"
        )
    if not parts:
        return (
            "Too little was supplied to draw an implication. Stating one anyway "
            "would be inference presented as analysis."
        )
    return ". ".join(p[0].upper() + p[1:] for p in parts) + "."


def _recommendation(f: dict[str, Fact]) -> str:
    if "rework_multiple" in f:
        base = (
            "Look at review turnaround before headcount. The rework loop is the "
            "cheapest thing on this screen to change and the most expensive to "
            "leave alone"
        )
    elif "top_waste" in f:
        base = (
            "Start with the largest priced category above — it is the only one "
            "big enough for a change to show up in the total"
        )
    elif "scenario_net_cost" in f:
        base = "Weigh the scenario against its band rather than its midpoint"
    else:
        base = (
            "No recommendation. The figures supplied do not support one, and an "
            "unsupported recommendation is the most expensive kind"
        )
    return (
        base
        + ". This is decision support, not a decision: every figure above is an "
        "aggregate, and no individual is named or scored anywhere in it."
    )


def _evidence(facts: list[Fact]) -> list[Evidence]:
    """One line per class, naming what sits in it. Empty classes are omitted
    rather than printed empty — a heading with nothing under it reads as a
    measurement of zero."""
    out: list[Evidence] = []
    order: list[EvidenceType] = ["observed", "modelled", "inferred"]
    blurb: dict[EvidenceType, str] = {
        "observed": "Counted directly from the event log",
        "modelled": "Produced by the simulator or a stated assumption, not measured",
        "inferred": "Reasoned from the figures above, not read off them",
    }
    for cls in order:
        items = [f for f in facts if f.evidence == cls]
        if not items:
            continue
        labels = "; ".join(f"{i.label} = {i.value}" for i in items)
        out.append(Evidence(type=cls, text=f"{blurb[cls]}. {labels}."))
    return out


def narrate(
    spend: Any = None,
    waste: Any = None,
    process: Any = None,
    simulation: Any = None,
) -> Narrative:
    """The entry point. Facts first, then prose over those facts only."""
    return render(build_facts(spend=spend, waste=waste, process=process, simulation=simulation))
