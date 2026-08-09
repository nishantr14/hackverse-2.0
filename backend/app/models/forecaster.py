"""
Cycle-time forecaster — BASELINE, not the final model.
Owner: Dipen (models lane).
Phase: Tier 2.

    python -m app.models.forecaster

THIS IS NOT THE LIGHTGBM MODEL THE PLAYBOOK (P9) SPECIFIES. It is empirical
quantiles over real historical cycle times, built to unblock the simulator
(P11) inside a two-hour window. `basis` says so on every response, and the
UI must render that string rather than implying a trained model.

What it does: reads v_cycle_time (frozen schema — first commit to merge, per
work item, already excluding items that never merged), and returns the P10 /
P50 / P90 of the matching historical group.

Fallback chain, widest-evidence-last, recorded in `basis`:
    component  ->  repo  ->  whole dataset
A group has to clear MIN_GROUP_SAMPLES before its own quantiles are trusted;
below that the numbers are noise dressed as precision.

Never returns a fabricated number. If the dataset itself is too thin, it
raises rather than inventing a default — the simulator surfacing "no
historical evidence" is honest; a hardcoded 40 hours is not.

No lines of code anywhere in here (CLAUDE.md #13), and no actor_hash — the
view it reads is per-work-item, so cycle-time evidence carries no identity
by construction.

The interface P11 calls is `forecast_cycle_time(session, repo=, component=)`.
When the real LightGBM model lands it replaces the body of that function and
changes `basis`; the signature and the CycleTimeForecast shape stay put, so
the simulator does not get rewritten.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.orm import Session

#: Below this, a group's own quantiles are noise — fall back to the wider
#: population instead. 20 is a judgement call, not a derived figure.
MIN_GROUP_SAMPLES = 20

#: Apache PRs can sit open for months; a handful of multi-thousand-hour
#: outliers drags P90 somewhere useless. Excluded rows are COUNTED and
#: reported, never silently dropped (playbook P9).
MAX_CYCLE_HOURS = 24 * 90.0

_CYCLE_HOURS_QUERY = """
    SELECT cycle_hours
      FROM v_cycle_time
     WHERE cycle_hours > 0
       {filters}
"""


class NoHistoricalEvidence(RuntimeError):
    """Raised instead of returning an invented estimate."""


@dataclass(frozen=True)
class CycleTimeForecast:
    p10_hours: float
    p50_hours: float
    p90_hours: float
    #: 'historical_quantiles_component' | '..._repo' | '..._global'.
    #: Always prefixed 'historical_quantiles' — never 'lightgbm'.
    basis: str
    n_samples: int
    #: Rows above MAX_CYCLE_HOURS dropped from this group, stated not hidden.
    n_excluded_outliers: int = 0
    assumptions: list[str] = field(default_factory=list)


def quantiles(values: list[float]) -> tuple[float, float, float]:
    """P10/P50/P90 by linear interpolation. Pure — the DB-free unit under test.

    statistics.quantiles would do this, but it needs n>=2 for n=10 cut points
    and returns 9 values we then index into; this is shorter than the guard.
    """
    if not values:
        raise NoHistoricalEvidence("no cycle times to take quantiles of")
    ordered = sorted(values)

    def q(p: float) -> float:
        if len(ordered) == 1:
            return ordered[0]
        pos = p * (len(ordered) - 1)
        lo = int(pos)
        hi = min(lo + 1, len(ordered) - 1)
        return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)

    return q(0.10), q(0.50), q(0.90)


def _fetch(session: Session, repo: str | None, component: str | None) -> list[float]:
    filters, params = "", {}
    if repo is not None:
        filters += " AND repo = :repo"
        params["repo"] = repo
    if component is not None:
        filters += " AND component = :component"
        params["component"] = component
    sql = _CYCLE_HOURS_QUERY.format(filters=filters)
    return [float(r[0]) for r in session.execute(text(sql), params)]


def forecast_cycle_time(
    session: Session,
    *,
    repo: str | None = None,
    component: str | None = None,
) -> CycleTimeForecast:
    """P10/P50/P90 cycle hours for the narrowest group with enough evidence.

    This is the interface P11 depends on. Keep the signature stable.
    """
    attempts: list[tuple[str, str | None, str | None]] = []
    if component is not None:
        attempts.append(("component", repo, component))
    if repo is not None:
        attempts.append(("repo", repo, None))
    attempts.append(("global", None, None))

    widened_from: str | None = None
    for scope, r, c in attempts:
        raw = _fetch(session, r, c)
        kept = [v for v in raw if v <= MAX_CYCLE_HOURS]
        if len(kept) < MIN_GROUP_SAMPLES and scope != "global":
            if widened_from is None:
                widened_from = scope
            continue
        if not kept:
            raise NoHistoricalEvidence(
                "v_cycle_time returned no usable rows — the event log has no "
                "merged work items yet, so no cycle time can be forecast."
            )
        p10, p50, p90 = quantiles(kept)
        notes = [
            "baseline: empirical historical quantiles, NOT the LightGBM model",
            f"minimum group size {MIN_GROUP_SAMPLES}",
            f"cycle times above {MAX_CYCLE_HOURS:.0f}h excluded as long-tail",
        ]
        if widened_from is not None:
            notes.append(
                f"requested {widened_from} had under {MIN_GROUP_SAMPLES} samples — "
                f"widened to {scope}"
            )
        return CycleTimeForecast(
            p10_hours=p10,
            p50_hours=p50,
            p90_hours=p90,
            basis=f"historical_quantiles_{scope}",
            n_samples=len(kept),
            n_excluded_outliers=len(raw) - len(kept),
            assumptions=notes,
        )

    raise NoHistoricalEvidence("unreachable: the global scope always returns or raises")


if __name__ == "__main__":  # pragma: no cover - operator convenience
    from app.db.session import get_read_session

    with get_read_session() as s:
        f = forecast_cycle_time(s)
        print(f"global   P50={f.p50_hours:8.1f}h  n={f.n_samples}  {f.basis}")
        for (repo,) in s.execute(text("SELECT DISTINCT repo FROM v_cycle_time")):
            g = forecast_cycle_time(s, repo=repo)
            print(f"{repo:9s} P50={g.p50_hours:8.1f}h  n={g.n_samples}  {g.basis}")
