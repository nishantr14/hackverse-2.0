"""
Umbrella-case detection - a read-time property, never a stored column.
Owner: Dipen (normalise + models lane).
Phase: Tier 0, advisory only.

WHY THIS EXISTS
---------------
Decision #6 stands and is not touched here: `ticket_key` is the case id, so
several pull requests sharing one Jira key are one case. On apache/kafka that
rule is right most of the time - a main PR plus its backport really is one unit
of work - but it has a long tail. `KAFKA-14133` is 18 PRs over 17 months
("Replace EasyMock with Mockito in streams tests"); `KAFKA-20444` is 15 PRs
titled `[1/N]` .. `[12/12]`. Those are work *programmes*. Their cycle time is a
programme duration, and averaged in with everything else they drag the
distribution that SpendView and the forecaster sit on.

So we name the shape rather than change the rule. Both the mapper's run report
and check 11 of `scripts/validate_ingest.py` import from here, so there is
exactly one definition of "umbrella" and it cannot drift between them.

WHY NOTHING IS PERSISTED
------------------------
`is_umbrella` is derived from `opened_at`, `closed_at` and a PR count that are
already stored. Writing it to `work_item` would need a schema change (frozen,
four teammates) and would freeze a judgement call into the data. Computed at
read time, re-tuning the threshold is a re-read, not a re-ingest.

WHAT IS A JUDGEMENT CALL
------------------------
Both constants below. The span rule is relative on purpose - an absolute "90
days" would mean something different on kafka than on a fast-moving repo,
whereas a multiple of the median re-calibrates itself to whatever was ingested.
Every consumer prints the multiple, the median and the resulting threshold next
to the count, so nobody has to open this file to know what "too long" meant.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime

#: A case whose span exceeds this many times the median case span is flagged.
#: Ten is "an order of magnitude beyond typical", chosen rather than measured:
#: at the time of writing the PR payloads had been cleared out of raw_payload,
#: so there was no live distribution to fit it to. Re-tune it against a real
#: ingest with `validate_ingest.py --span-multiple`, which takes this as its
#: default and prints whatever it was given.
UMBRELLA_SPAN_MULTIPLE = 10.0

#: A case carrying this many pull requests is a work programme whatever its
#: span. Eight is where the measured kafka distribution stops being a main PR
#: plus backports (1-3 PRs covers 2,480 of 2,562 cases) and starts being
#: explicitly numbered `[N/M]` series.
UMBRELLA_MIN_PRS = 8


@dataclass(frozen=True)
class CaseSpan:
    """One case's span, in the two terms the umbrella rule is written in.

    `days` is None when the case has no measurable span - still open, or
    missing a date. Such a case is never flagged by span and never counts
    toward the median; a case that has not finished has no duration yet, and
    treating "unknown" as "short" would drag the median down.

    `n_prs` is None when the caller cannot know it. The validator reads
    `work_item`, which keeps one `source_ref` and no list of PR numbers, so it
    passes None and the count half of the rule simply does not fire there.
    """

    work_item_id: str
    days: float | None
    n_prs: int | None = None
    case_source: str | None = None


@dataclass(frozen=True)
class SpanReport:
    """Everything a caller needs to print the finding and justify the rule."""

    n_cases: int
    n_measurable: int
    median_days: float | None
    multiple: float
    min_prs: int
    #: None when there is nothing to scale: no closed case, or a median of
    #: zero. A multiple of zero is zero, which would flag every case that
    #: lasted a single second, so we decline to judge instead of judging wrong.
    threshold_days: float | None
    over_span: tuple[CaseSpan, ...]
    over_pr_count: tuple[CaseSpan, ...]
    #: The union, longest span first. This is the `is_umbrella` set.
    umbrellas: tuple[CaseSpan, ...]

    @property
    def unscalable(self) -> bool:
        return self.threshold_days is None

    def top(self, n: int) -> tuple[CaseSpan, ...]:
        """The n longest-running cases with a measurable span."""
        return self.umbrellas[:n]


def span_days(opened: datetime | None, closed: datetime | None) -> float | None:
    """Calendar days between the two, or None if either is missing.

    Negative spans are returned as they are rather than clamped: a closed_at
    before opened_at is a data bug, and hiding it here would send it downstream
    silently. Check 2 of the validator is what catches that shape.
    """
    if opened is None or closed is None:
        return None
    return (closed - opened).total_seconds() / 86400.0


def median_span_days(cases: list[CaseSpan]) -> float | None:
    """Median over cases with a measurable span. None if there are none."""
    spans = [c.days for c in cases if c.days is not None]
    return statistics.median(spans) if spans else None


def threshold_from(median: float | None, multiple: float) -> float | None:
    """The span above which a case is an umbrella. See `SpanReport.threshold_days`."""
    if median is None or median <= 0:
        return None
    return median * multiple


def is_umbrella(
    case: CaseSpan, threshold_days: float | None, min_prs: int = UMBRELLA_MIN_PRS
) -> bool:
    """The rule itself: span over threshold OR enough PRs to be a programme."""
    if case.n_prs is not None and case.n_prs >= min_prs:
        return True
    return (
        threshold_days is not None
        and case.days is not None
        and case.days > threshold_days
    )


def summarise(
    cases: list[CaseSpan],
    *,
    multiple: float = UMBRELLA_SPAN_MULTIPLE,
    min_prs: int = UMBRELLA_MIN_PRS,
) -> SpanReport:
    """Apply the rule to every case and report it. Pure; no I/O, no ordering
    dependence - the output sorts by span then id, so two runs over the same
    cases in different orders produce the same report."""
    median = median_span_days(cases)
    threshold = threshold_from(median, multiple)

    over_span = [
        c
        for c in cases
        if threshold is not None and c.days is not None and c.days > threshold
    ]
    over_prs = [c for c in cases if c.n_prs is not None and c.n_prs >= min_prs]

    def order(c: CaseSpan) -> tuple[float, str]:
        # Longest first; unmeasurable spans last, then by id so the tie-break
        # never depends on which order the caller assembled the list in.
        return (-(c.days if c.days is not None else float("-inf")), c.work_item_id)

    umbrellas = sorted(
        {c.work_item_id: c for c in (*over_span, *over_prs)}.values(), key=order
    )

    return SpanReport(
        n_cases=len(cases),
        n_measurable=sum(1 for c in cases if c.days is not None),
        median_days=median,
        multiple=multiple,
        min_prs=min_prs,
        threshold_days=threshold,
        over_span=tuple(sorted(over_span, key=order)),
        over_pr_count=tuple(sorted(over_prs, key=order)),
        umbrellas=tuple(umbrellas),
    )
