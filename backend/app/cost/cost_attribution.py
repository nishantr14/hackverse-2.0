"""
Cost attribution — joins inferred sessions + rate card into cost_event rows.
Owner: Diljit (cost lane).
Phase: Tier 1.

    python -m app.cost.cost_attribution             # write cost_event
    python -m app.cost.cost_attribution --dry-run   # report only

Determinism discipline: this is where a rupee figure is actually computed.
Every number the API/UI shows must trace back here (or to another module like
it), never to an LLM call.

THREE OF THE FOUR BASES LIVE HERE

    session_inferred  work_session.hours x rate_card.hourly for the actor's
                      INFERRED band. Real engineer time.
    ci_runner         ci_run.runner_minutes x the published GitHub Actions
                      per-minute price. Machine time.
    meeting           calendar_event attendee-hours x a blended rate.

The fourth, `ai_tokens`, is written by app.synthetic.gen_tokens, because the
token counts and the prices are generated together and splitting them would
mean the cost could disagree with the tokens it came from.

WHY ci_runner ROWS CARRY cost BUT NOT hours
    `v_case_cost.total_hours` sums `hours` across every basis. A runner minute
    is not an engineer minute, and adding the two produces a number that means
    nothing while looking authoritative — 73,226 CI runs would swamp the real
    engineer time. So CI rows price the minutes and leave `hours` NULL. The
    minutes stay in `ci_run`, which is where the CI waste detector reads them.

WHY MEETINGS ARE PRICED AT A BLENDED RATE
    `calendar_event` has no attendee list — that absence is the privacy
    guarantee (decision #9, and docs/schema.sql says so permanently). So we
    know how many people were in the room and for how long, but never which
    people. Pricing therefore uses one blended hourly rate derived from the
    inferred band distribution. It is an assumption, it is stated, and it is
    the only option that does not require re-identifying attendees.

Idempotent: `cost_event_id` is derived from the source row's own primary key,
so re-running upserts in place rather than doubling the spend.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import yaml
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.cost.rate_card import RATES_PATH, RateCardError
from app.db.session import write_session
from app.waste.common import citation_for

#: cost_event.basis values this module owns. 'ai_tokens' is deliberately absent.
BASES: tuple[str, ...] = ("session_inferred", "ci_runner", "meeting")

#: Money is rounded once, at write time, to whole paise.
QUANT = Decimal("0.01")


def _round(value: Decimal) -> Decimal:
    return value.quantize(QUANT, rounding=ROUND_HALF_UP)


def load_config() -> dict[str, Any]:
    if not RATES_PATH.exists():
        raise RateCardError(f"{RATES_PATH} does not exist")
    return yaml.safe_load(RATES_PATH.read_text(encoding="utf-8")) or {}


# ---------------------------------------------------------------------
# session_inferred
# ---------------------------------------------------------------------

SESSION_SQL = """
INSERT INTO cost_event
    (cost_event_id, event_id, work_item_id, actor_hash, hours, rate_band, cost, basis)
SELECT
    'sess:' || ws.session_id,
    NULL,
    ws.work_item_id,
    ws.actor_hash,
    ws.hours,
    rc.hourly,
    ROUND(ws.hours * rc.hourly, 2),
    'session_inferred'
FROM work_session ws
JOIN actor a      ON a.actor_hash = ws.actor_hash
JOIN rate_card rc ON rc.role_band = a.role_band
ON CONFLICT (cost_event_id) DO UPDATE SET
    hours      = EXCLUDED.hours,
    rate_band  = EXCLUDED.rate_band,
    cost       = EXCLUDED.cost,
    actor_hash = EXCLUDED.actor_hash
"""

#: An actor with no inferred band would be silently dropped by the JOIN above,
#: taking their hours out of the spend total without anything saying so.
UNPRICED_SESSIONS_SQL = """
SELECT count(*), COALESCE(SUM(ws.hours), 0)
FROM work_session ws
JOIN actor a ON a.actor_hash = ws.actor_hash
LEFT JOIN rate_card rc ON rc.role_band = a.role_band
WHERE rc.role_band IS NULL
"""


# ---------------------------------------------------------------------
# ci_runner
# ---------------------------------------------------------------------

CI_SQL = """
INSERT INTO cost_event
    (cost_event_id, event_id, work_item_id, actor_hash, hours, rate_band, cost, basis)
SELECT
    'ci:' || r.run_id,
    NULL,
    r.work_item_id,
    NULL,                    -- machine time belongs to no person
    NULL,                    -- see module docstring: not engineer hours
    NULL,                    -- not a salary band
    ROUND(r.runner_minutes * CAST(:rate AS NUMERIC), 2),
    'ci_runner'
FROM ci_run r
WHERE r.work_item_id IS NOT NULL
  AND r.runner_minutes > 0
ON CONFLICT (cost_event_id) DO UPDATE SET
    cost = EXCLUDED.cost
"""


# ---------------------------------------------------------------------
# meeting
# ---------------------------------------------------------------------

#: One rate for every attendee, because we do not know — and must not learn —
#: who was in the room. Weighted by how many actors sit in each inferred band.
BLENDED_RATE_SQL = """
SELECT COALESCE(SUM(rc.hourly) / NULLIF(count(*), 0), 0)
FROM actor a
JOIN rate_card rc ON rc.role_band = a.role_band
"""

MEETING_SQL = """
INSERT INTO cost_event
    (cost_event_id, event_id, work_item_id, actor_hash, hours, rate_band, cost, basis)
SELECT
    'meet:' || c.meeting_id,
    NULL,
    c.work_item_id,
    NULL,                    -- deliberately null: no attendee list exists
    c.attendee_count * c.duration_min / 60.0,
    CAST(:blended AS NUMERIC),
    ROUND(
        (c.attendee_count * c.duration_min / 60.0) * CAST(:blended AS NUMERIC), 2
    ),
    'meeting'
FROM calendar_event c
WHERE c.work_item_id IS NOT NULL
ON CONFLICT (cost_event_id) DO UPDATE SET
    hours     = EXCLUDED.hours,
    rate_band = EXCLUDED.rate_band,
    cost      = EXCLUDED.cost
"""


# ---------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------


def ci_rate(cfg: dict[str, Any]) -> Decimal:
    """Per-runner-minute price. Fails closed if the citation is incomplete."""
    citation_for(cfg, "ci_cost")
    rate = (cfg.get("ci_cost") or {}).get("cost_per_runner_minute")
    if rate is None:
        raise RateCardError(
            f"ci_cost.cost_per_runner_minute is not set in {RATES_PATH}. "
            "CI minutes are real and measured; pricing them needs the published "
            "per-minute figure, not an estimate."
        )
    return Decimal(str(rate))


def blended_rate(session: Session) -> Decimal:
    value = session.execute(text(BLENDED_RATE_SQL)).scalar()
    return Decimal(str(value or 0))


def attribute(session: Session, cfg: dict[str, Any]) -> dict[str, int]:
    """Write every cost_event row this module owns. Returns rows per basis."""
    written: dict[str, int] = {}

    written["session_inferred"] = session.execute(text(SESSION_SQL)).rowcount
    written["ci_runner"] = session.execute(
        text(CI_SQL), {"rate": ci_rate(cfg)}
    ).rowcount
    written["meeting"] = session.execute(
        text(MEETING_SQL), {"blended": blended_rate(session)}
    ).rowcount
    return written


def report(session: Session, cfg: dict[str, Any]) -> None:
    print("\n  COST ATTRIBUTION")
    print(f"  {'basis':18} {'rows':>9} {'hours':>12} {'cost (INR)':>18}")
    total = Decimal(0)
    for basis, rows, hours, cost in session.execute(
        text(
            "SELECT basis, count(*), COALESCE(SUM(hours),0), COALESCE(SUM(cost),0) "
            "FROM cost_event GROUP BY basis ORDER BY 4 DESC"
        )
    ):
        total += Decimal(str(cost))
        hours_text = f"{float(hours):>12,.0f}" if hours else f"{'—':>12}"
        print(f"  {basis:18} {rows:>9,} {hours_text} {float(cost):>18,.2f}")
    print(f"  {'TOTAL':18} {'':>9} {'':>12} {float(total):>18,.2f}")
    print(f"  {'':18} {'':>9} {'':>12} {inr(total):>18}")

    unpriced, unpriced_hours = session.execute(text(UNPRICED_SESSIONS_SQL)).one()
    if unpriced:
        print(
            f"\n  !! {unpriced:,} work_session rows ({float(unpriced_hours):,.0f} h) "
            "have no rate_card row for their band and are NOT in the total above.\n"
            "     Run `python -m app.cost.band_inference` and re-run this."
        )

    print(f"\n  labour rates   {citation_for(cfg, 'rate_card')}")
    print(f"  CI rate        {citation_for(cfg, 'ci_cost')}")
    print(
        "  meetings       blended rate across inferred bands; calendar_event\n"
        "                 carries no attendee list, so per-person pricing is\n"
        "                 not possible by construction. State it as modelled."
    )


def inr(amount: Decimal) -> str:
    """Rupees the way the audience reads them: lakh and crore, never millions."""
    value = float(amount)
    if abs(value) >= 1e7:
        return f"₹{value / 1e7:,.2f} crore"
    if abs(value) >= 1e5:
        return f"₹{value / 1e5:,.2f} lakh"
    return f"₹{value:,.2f}"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what is already there without writing",
    )
    args = parser.parse_args(argv)

    # This module prints rupees, and the Windows console defaults to cp1252,
    # which has no code point for U+20B9. Without this the numbers compute
    # correctly and the process still dies on the way to the screen.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    try:
        cfg = load_config()
        with write_session() as session:
            if not args.dry_run:
                written = attribute(session, cfg)
                session.commit()
                print(
                    "  wrote "
                    + ", ".join(f"{n:,} {b}" for b, n in written.items())
                    + " cost_event rows"
                )
            report(session, cfg)
    except RateCardError as exc:
        print(f"\n  CANNOT PRICE\n  {exc}\n", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
