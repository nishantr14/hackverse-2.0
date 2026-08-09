"""
CI waste — runner minutes, priced where a citation exists.
Owner: Diljit (waste lane).
Phase: Tier 1.

    python -m app.waste.ci_waste

Runner-minutes on a rerun (attempt > 1) or a failed run are this database's
strongest waste signal: no session inference, no estimate of who did what,
just wall-clock minutes GitHub Actions actually burned on a run that either
had to happen again or told nobody anything useful.
v_ci_waste_minutes (migrations/003_process_and_waste_views.sql) computes
this straight from ci_run.

Pricing needs two citations that do not exist yet — config/rates.yaml's
`ci_cost.cost_per_runner_minute` and `carbon.grid_kg_co2_per_kwh` +
`runner_power_watts` — so both conversions fail closed rather than
inventing a number. Minutes are always computed and returned regardless of
whether pricing is available.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.cost.rate_card import RateCardError, load_config
from app.db.session import write_session
from app.waste.common import WasteFinding, citation_for

EVIDENCE_QUERY = """
    SELECT run_id, work_item_id, repo, attempt, conclusion, runner_minutes,
           is_rerun, is_failure
      FROM v_ci_waste_minutes
"""

MINUTES_PER_HOUR = Decimal(60)
WATTS_PER_KW = Decimal(1000)


def total_minutes(session: Session) -> dict[str, Decimal]:
    row = session.execute(
        text(
            "SELECT COALESCE(SUM(runner_minutes) FILTER (WHERE is_rerun), 0),"
            "       COALESCE(SUM(runner_minutes) FILTER (WHERE is_failure), 0),"
            "       COALESCE(SUM(runner_minutes), 0)"
            "  FROM v_ci_waste_minutes"
        )
    ).one()
    return {
        "rerun": Decimal(row[0]),
        "failure": Decimal(row[1]),
        "total": Decimal(row[2]),
    }


def price(minutes: Decimal, cfg: dict[str, Any]) -> tuple[Decimal | None, str | None]:
    """(cost, reason) — cost is None with a reason when uncited, never a guess."""
    try:
        citation_for(cfg, "ci_cost")
    except RateCardError as exc:
        return None, str(exc)
    rate = (cfg.get("ci_cost") or {}).get("cost_per_runner_minute")
    if rate is None:
        return None, "ci_cost.cost_per_runner_minute is not set"
    return (minutes * Decimal(str(rate))).quantize(Decimal("0.01")), None


def carbon_kg(minutes: Decimal, cfg: dict[str, Any]) -> tuple[Decimal | None, str | None]:
    try:
        citation_for(cfg, "carbon")
    except RateCardError as exc:
        return None, str(exc)
    c = cfg.get("carbon") or {}
    grid = c.get("grid_kg_co2_per_kwh")
    watts = c.get("runner_power_watts")
    if grid is None or watts is None:
        return None, "carbon.grid_kg_co2_per_kwh or runner_power_watts is not set"
    kwh = (minutes / MINUTES_PER_HOUR) * (Decimal(str(watts)) / WATTS_PER_KW)
    return (kwh * Decimal(str(grid))).quantize(Decimal("0.001")), None


def detect(session: Session, cfg: dict[str, Any] | None = None) -> WasteFinding:
    minutes = total_minutes(session)
    cfg = cfg if cfg is not None else load_config()
    cost, cost_reason = price(minutes["total"], cfg)
    co2, co2_reason = carbon_kg(minutes["total"], cfg)
    co2_text = f"{float(co2):,.1f}" if co2 is not None else f"unavailable ({co2_reason})"
    note = (
        f"{float(minutes['rerun']):,.0f} rerun minutes + "
        f"{float(minutes['failure']):,.0f} failed-run minutes, wall clock "
        "(not billable minutes — the per-run timing endpoint would exhaust "
        "the REST budget; overstates queued runs, understates parallel ones). "
        f"kgCO2e: {co2_text}."
    )
    return WasteFinding(
        detector="ci_waste",
        hours=float(minutes["total"] / MINUTES_PER_HOUR),
        cost=float(cost) if cost is not None else None,
        unit_note=note,
        evidence_query=EVIDENCE_QUERY,
        cost_pending=cost_reason,
    )


def main() -> int:
    with write_session() as session:
        finding = detect(session)
    print("\n  CI WASTE")
    print(f"    hours     {finding.hours:,.1f}")
    print(f"    cost      {finding.cost if finding.cost is not None else 'pending citation'}")
    print(f"    note      {finding.unit_note}")
    if finding.cost_pending:
        print(f"    PENDING   {finding.cost_pending}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
