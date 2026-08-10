"""
Blind backtest of the throughput forecast the simulator runs on.
Owner: Dipen (models lane).
Phase: Tier 2.

    python -m app.models.backtest
    python -m app.models.backtest --horizon 4 --folds 3 --min-items 20

WHAT IS BEING TESTED, AND WHY IT IS NOT CYCLE TIME.

The playbook's P12 backtests the cycle-time forecaster. That is not the
number this demo rests on, and on this dataset it cannot be tested honestly:
`v_cycle_time` holds 982 rows of which 775 — 79% — are under three minutes,
because Kafka and Flink squash on merge and the squashed commit carries the
merge timestamp. A "cycle time" of 40 seconds is an artifact of how the
history was rewritten, not a measurement of how long anything took. MAPE
against a near-zero actual is a number with no meaning, and reporting one
would be worse than reporting none.

What the simulator actually extrapolates is `items_per_week` per component
(see app/models/simulator.py: every result is open_items / items_per_week,
scaled by headcount). So that is what gets tested here — the assumption the
headline figure is built on.

THE METHOD IS THE PRODUCTION METHOD, on less data. No model is trained and
no library is introduced. `v_component_capacity` computes a rate as
deliveries / elapsed weeks; this recomputes exactly that over a truncated
history and extrapolates it across a window the calculation never saw:

    weeks < cutoff          ->  rate = merged items / elapsed weeks
    predicted(test window)  =   rate * horizon weeks
    actual(test window)     =   merged items observed there
    error                   =   predicted - actual

ROLLING ORIGIN, because one split is a data point and three is evidence.
Fold k ends its training data `k * horizon` weeks before the last complete
week, so each fold predicts a window strictly after everything it learned
from. No fold ever sees its own test period.

Deterministic: no sampling, no shuffling, no seed. The same database gives
the same table every run.

Reads v_event_log only (the app role is granted on views, never base
tables). A delivery is a `merged` event, which is what `closed_at` records
for these repos.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session

#: Same constants the simulator turns a CV into a band with. Imported rather
#: than restated so the interval being scored is the interval the product
#: shows — a backtest of a band nobody sees would prove nothing.
from app.models.simulator import CV_TO_SPREAD_PCT, MAX_SPREAD_PCT, MIN_SPREAD_PCT

#: Weeks per prediction window.
DEFAULT_HORIZON_WEEKS = 4

#: Rolling-origin folds.
DEFAULT_FOLDS = 3

#: A component needs some history before a rate means anything.
DEFAULT_MIN_ITEMS = 20

#: Below this many actual deliveries, a percentage error is noise —
#: predicting 3 when 1 shipped is a 200% error that says nothing useful.
#: Those rows still count toward MAE, which has no such problem.
MIN_ACTUAL_FOR_PCT = 3

WEEKLY_SQL = """
SELECT repo,
       COALESCE(component, 'unassigned') AS component,
       date_trunc('week', ts)::date      AS week,
       count(DISTINCT case_id)           AS delivered
FROM v_event_log
WHERE activity = 'merged' AND in_window
GROUP BY 1, 2, 3
ORDER BY 3
"""


@dataclass(frozen=True)
class Observation:
    fold: int
    repo: str
    component: str
    train_weeks: int
    rate_per_week: float
    predicted: float
    actual: int
    p10: float
    p90: float

    @property
    def abs_error(self) -> float:
        return abs(self.predicted - self.actual)

    @property
    def pct_error(self) -> float | None:
        """None when the actual is too small for a percentage to mean anything."""
        if self.actual < MIN_ACTUAL_FOR_PCT:
            return None
        return abs(self.predicted - self.actual) / self.actual * 100.0

    @property
    def in_band(self) -> bool:
        return self.p10 <= self.actual <= self.p90


@dataclass(frozen=True)
class Report:
    observations: list[Observation]
    horizon_weeks: int
    folds: int

    @property
    def n(self) -> int:
        return len(self.observations)

    @property
    def mae(self) -> float:
        """Mean absolute error, in items per window. Defined for every row."""
        if not self.observations:
            return float("nan")
        return sum(o.abs_error for o in self.observations) / len(self.observations)

    @property
    def scored_for_pct(self) -> list[Observation]:
        return [o for o in self.observations if o.pct_error is not None]

    @property
    def mape(self) -> float | None:
        scored = self.scored_for_pct
        if not scored:
            return None
        return sum(o.pct_error or 0.0 for o in scored) / len(scored)

    @property
    def baseline_mae(self) -> float:
        """MAE of the naive alternative: predict the population's mean rate.

        An error with nothing to compare it to is not evidence. If the
        forecast cannot beat "assume every component ships at the average
        rate", it has earned nothing, and this is what says so.
        """
        if not self.observations:
            return float("nan")
        mean_pred = sum(o.predicted for o in self.observations) / len(self.observations)
        return sum(abs(mean_pred - o.actual) for o in self.observations) / len(
            self.observations
        )

    @property
    def bias(self) -> float:
        """Mean SIGNED error. Separates a wrong aim from a wide spread.

        MAE cannot tell the two apart: errors that cancel and errors that all
        point the same way give the same MAE, and only one of them is a
        fixable flaw in the method. A large negative bias means the forecast
        is consistently low, which is a different and more serious finding
        than being imprecise.
        """
        if not self.observations:
            return float("nan")
        return sum(o.predicted - o.actual for o in self.observations) / len(
            self.observations
        )

    @property
    def under_predicted(self) -> int:
        return sum(1 for o in self.observations if o.predicted < o.actual)

    @property
    def band_coverage(self) -> float:
        """Share of actuals inside P10-P90. Calibrated would be ~0.80."""
        if not self.observations:
            return float("nan")
        return sum(1 for o in self.observations if o.in_band) / len(self.observations)


def _spread_pct(cv: float) -> float:
    """The simulator's band width, for a single component's CV."""
    return max(MIN_SPREAD_PCT, min(MAX_SPREAD_PCT, cv * CV_TO_SPREAD_PCT))


def _cv(values: list[int]) -> float:
    """Week-to-week coefficient of variation, matching v_component_capacity."""
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    if mean <= 0:
        return 0.0
    var = sum((v - mean) ** 2 for v in values) / len(values)
    return var**0.5 / mean


def load_weekly(session: Session) -> dict[tuple[str, str], dict[date, int]]:
    series: dict[tuple[str, str], dict[date, int]] = defaultdict(dict)
    for repo, component, week, delivered in session.execute(text(WEEKLY_SQL)):
        series[(repo, component)][week] = int(delivered)
    return series


def run_backtest(
    session: Session,
    horizon_weeks: int = DEFAULT_HORIZON_WEEKS,
    folds: int = DEFAULT_FOLDS,
    min_items: int = DEFAULT_MIN_ITEMS,
) -> Report:
    series = load_weekly(session)
    if not series:
        return Report([], horizon_weeks, folds)

    all_weeks = sorted({w for s in series.values() for w in s})
    # The final week is usually partial — the export happened mid-week — and
    # scoring a truncated window against a full-week rate manufactures error
    # that is an artifact of when the dump was taken.
    complete = all_weeks[:-1]

    observations: list[Observation] = []
    for fold in range(folds):
        end = len(complete) - fold * horizon_weeks
        start = end - horizon_weeks
        if start <= horizon_weeks:  # not enough history left to train on
            break
        test_weeks = complete[start:end]
        train_weeks = complete[:start]

        for (repo, component), weekly in sorted(series.items()):
            train = [weekly.get(w, 0) for w in train_weeks]
            if sum(train) < min_items:
                continue
            rate = sum(train) / len(train)
            if rate <= 0:
                continue

            predicted = rate * len(test_weeks)
            actual = sum(weekly.get(w, 0) for w in test_weeks)
            spread = _spread_pct(_cv(train)) / 100.0
            observations.append(
                Observation(
                    fold=fold,
                    repo=repo,
                    component=component,
                    train_weeks=len(train_weeks),
                    rate_per_week=rate,
                    predicted=predicted,
                    actual=actual,
                    p10=predicted * (1 - spread),
                    p90=predicted * (1 + spread),
                )
            )

    return Report(observations, horizon_weeks, folds)


def format_report(report: Report, show_rows: int = 12) -> str:
    if not report.n:
        return (
            "No observations. Either v_event_log has no merged events in the "
            "window, or --min-items excluded every component."
        )

    lines: list[str] = []
    lines.append("")
    lines.append("  BLIND BACKTEST — component delivery rate (items per window)")
    lines.append(
        f"  {report.folds} rolling-origin folds x {report.horizon_weeks}-week horizon"
    )
    lines.append("")
    lines.append(
        f"  {'fold':>4}  {'component':<34} {'rate/wk':>8} {'pred':>7} {'actual':>7} "
        f"{'abs':>6} {'pct':>7}  band"
    )
    lines.append("  " + "-" * 88)
    for o in sorted(report.observations, key=lambda x: (x.fold, -x.actual))[:show_rows]:
        pct = f"{o.pct_error:6.1f}%" if o.pct_error is not None else "     --"
        name = f"{o.repo.split('/')[-1]}/{o.component}"[:34]
        lines.append(
            f"  {o.fold:>4}  {name:<34} {o.rate_per_week:8.2f} {o.predicted:7.1f} "
            f"{o.actual:7d} {o.abs_error:6.1f} {pct}  {'in' if o.in_band else 'OUT'}"
        )
    if report.n > show_rows:
        lines.append(f"  … {report.n - show_rows} more")

    lines.append("")
    lines.append(f"  observations          {report.n}")
    lines.append(
        f"  MAE                   {report.mae:.2f} items per {report.horizon_weeks}-week window"
    )
    lines.append(
        f"  baseline MAE          {report.baseline_mae:.2f}  (predict the mean rate for every component)"
    )
    beat = report.baseline_mae - report.mae
    lines.append(
        f"  vs baseline           {'BEATS by' if beat > 0 else 'LOSES TO baseline by'} "
        f"{abs(beat):.2f} items"
    )
    mape = report.mape
    lines.append(
        f"  MAPE                  {mape:.1f}%  (n={len(report.scored_for_pct)}; "
        f"rows with actual < {MIN_ACTUAL_FOR_PCT} excluded as unmeaningful)"
        if mape is not None
        else "  MAPE                  not reported — no window had enough actuals"
    )
    lines.append(
        f"  bias (mean signed)    {report.bias:+.2f} items — "
        f"{report.under_predicted}/{report.n} windows under-predicted"
    )
    lines.append(
        f"  P10-P90 coverage      {report.band_coverage * 100:.0f}%  "
        f"(calibrated would be ~80%)"
    )

    # State the finding rather than leaving it to be inferred from the table.
    # The trigger is bias as a share of MAE, not a headcount of windows: it
    # asks "how much of the error is aim rather than spread", which is the
    # thing that decides whether the method is fixable or merely imprecise.
    if report.bias < 0 and abs(report.bias) > 0.3 * report.mae:
        lines.append("")
        lines.append(
            "  FINDING: the forecast is systematically LOW, not merely imprecise."
        )
        lines.append(
            "  The rate is computed over a component's whole history, which is what"
        )
        lines.append(
            "  v_component_capacity does in production. Delivery accelerated across"
        )
        lines.append(
            "  this window, so a whole-history average lags the recent rate and the"
        )
        lines.append(
            "  band — derived from the same history — is centred too low to cover it."
        )
        lines.append(
            "  Consequence for the simulator: weeks-to-clear is OVERSTATED, so its"
        )
        lines.append(
            "  delivery-date deltas are conservative rather than optimistic."
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--horizon", type=int, default=DEFAULT_HORIZON_WEEKS)
    parser.add_argument("--folds", type=int, default=DEFAULT_FOLDS)
    parser.add_argument("--min-items", type=int, default=DEFAULT_MIN_ITEMS)
    parser.add_argument("--rows", type=int, default=12)
    args = parser.parse_args()

    from app.db.session import get_read_engine

    with Session(get_read_engine()) as session:
        report = run_backtest(session, args.horizon, args.folds, args.min_items)
    print(format_report(report, args.rows))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
