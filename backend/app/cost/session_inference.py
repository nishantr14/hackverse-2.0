"""
Session inference — time-clustered effort model.
Owner: Diljit (cost lane).
Phase: Tier 1.

    python -m app.cost.session_inference            # write work_session
    python -m app.cost.session_inference --dry-run   # report only

Infers work sessions — and therefore hours — by clustering event_log
timestamps per actor, NOT by counting lines of code. LoC is not a cost
signal here and must not be used as one (decision #13).

    gap threshold     events under SESSION_GAP_MINUTES apart belong to the
                       same session
    lead-in           SESSION_LEAD_IN_MINUTES added before a session's first
                       event, for the work that precedes a commit
    daily cap         a session's hours are scaled down, not truncated, if an
                       actor's total for that day exceeds SESSION_DAILY_CAP_HOURS

A "session" is a contiguous stretch of one actor's activity and may touch
more than one work item — its hours are split across them proportional to
event count, because that is the only signal we have for how the time was
actually divided. Each (session, work_item) pair becomes one work_session row.

ci_run is excluded: it is a machine event with no human attached, and
including it would stretch sessions across automated activity that involved
nobody.
"""

from __future__ import annotations

import argparse
import hashlib
import statistics
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import WorkSession
from app.db.session import write_session

#: Gap settings for the sensitivity table. Not applied to the written rows —
#: see report_gap_sensitivity. The configured value is what actually gets used.
#: Range matches Gate B's own investigation: 90 (the original default) never
#: reproduced a plausible engineer-years figure on its own (median inter-
#: commit gap on this data is ~7.9h — real async OSS work, not a bug), so the
#: sweep runs out past a full day to show the whole shape, not just three
#: points clustered at the low end.
GAP_SWEEP_MINUTES: tuple[int, ...] = (90, 240, 480, 1440)

#: Hours in a working year, for the engineer-years sanity check (Gate B).
HOURS_PER_ENGINEER_YEAR = 1840

EVENTS_SQL = """
    SELECT actor_hash, work_item_id, event_id, ts
      FROM event_log
     WHERE actor_hash IS NOT NULL AND activity <> 'ci_run'
     ORDER BY actor_hash, ts
"""


@dataclass(frozen=True)
class RawEvent:
    actor_hash: str
    work_item_id: str
    event_id: str
    ts: datetime


@dataclass(frozen=True)
class Cluster:
    """One contiguous stretch of one actor's activity."""

    actor_hash: str
    started_at: datetime  # first event minus lead-in
    ended_at: datetime  # last event, unmodified
    events: tuple[RawEvent, ...]

    @property
    def raw_hours(self) -> float:
        return (self.ended_at - self.started_at).total_seconds() / 3600.0

    @property
    def day(self) -> date:
        return self.started_at.date()


def load_events(session: Session) -> list[RawEvent]:
    rows = session.execute(text(EVENTS_SQL)).all()
    return [
        RawEvent(actor_hash=r[0], work_item_id=r[1], event_id=r[2], ts=r[3])
        for r in rows
    ]


def cluster_events(
    events: Sequence[RawEvent], gap_minutes: float, lead_in_minutes: float
) -> list[Cluster]:
    """Events must already be sorted by (actor_hash, ts) — load_events does."""
    clusters: list[Cluster] = []
    current: list[RawEvent] = []
    gap = timedelta(minutes=gap_minutes)
    lead_in = timedelta(minutes=lead_in_minutes)

    def flush() -> None:
        if current:
            clusters.append(
                Cluster(
                    actor_hash=current[0].actor_hash,
                    started_at=current[0].ts - lead_in,
                    ended_at=current[-1].ts,
                    events=tuple(current),
                )
            )

    for ev in events:
        if current and (
            ev.actor_hash != current[-1].actor_hash or ev.ts - current[-1].ts > gap
        ):
            flush()
            current = []
        current.append(ev)
    flush()
    return clusters


def apply_daily_cap(clusters: Sequence[Cluster], cap_hours: float) -> list[float]:
    """Capped hours per cluster, same order as `clusters`.

    Scaled proportionally across the whole day, not truncated in iteration
    order — a long first session shouldn't be the one that "eats" the cap
    while a short session later that day sails through untouched.
    """
    raw = [c.raw_hours for c in clusters]
    by_day: dict[tuple[str, date], list[int]] = defaultdict(list)
    for i, c in enumerate(clusters):
        by_day[(c.actor_hash, c.day)].append(i)

    capped = [0.0] * len(clusters)
    for indices in by_day.values():
        total = sum(raw[i] for i in indices)
        factor = min(1.0, cap_hours / total) if total > cap_hours else 1.0
        for i in indices:
            capped[i] = raw[i] * factor
    return capped


def session_id_for(actor_hash: str, cluster_start: datetime, work_item_id: str) -> str:
    key = f"{actor_hash}|{cluster_start.isoformat()}|{work_item_id}"
    return hashlib.sha256(key.encode()).hexdigest()[:24]


def split_by_work_item(
    clusters: Sequence[Cluster], capped_hours: Sequence[float]
) -> list[dict]:
    """One row per (cluster, work_item) touched in it, hours proportional to
    the event count on that item within the cluster."""
    rows: list[dict] = []
    for c, hours in zip(clusters, capped_hours):
        per_item: dict[str, int] = defaultdict(int)
        for ev in c.events:
            per_item[ev.work_item_id] += 1
        total_events = len(c.events)
        for work_item_id, n_events in per_item.items():
            share = hours * (n_events / total_events)
            rows.append(
                {
                    "session_id": session_id_for(
                        c.actor_hash, c.started_at, work_item_id
                    ),
                    "actor_hash": c.actor_hash,
                    "work_item_id": work_item_id,
                    "started_at": c.started_at,
                    "ended_at": c.ended_at,
                    "hours": round(share, 4),
                    "n_events": n_events,
                }
            )
    return rows


def build_sessions(
    events: Sequence[RawEvent],
    gap_minutes: float,
    lead_in_minutes: float,
    cap_hours: float,
) -> tuple[list[Cluster], list[float], list[dict]]:
    clusters = cluster_events(events, gap_minutes, lead_in_minutes)
    capped = apply_daily_cap(clusters, cap_hours)
    rows = split_by_work_item(clusters, capped)
    return clusters, capped, rows


def write_rows(session: Session, rows: Sequence[dict]) -> None:
    for start in range(0, len(rows), 1000):
        chunk = rows[start : start + 1000]
        if not chunk:
            continue
        stmt = insert(WorkSession).values(chunk)
        session.execute(
            stmt.on_conflict_do_update(
                index_elements=["session_id"],
                set_={
                    c: getattr(stmt.excluded, c) for c in chunk[0] if c != "session_id"
                },
            )
        )
    session.commit()


# ---------------------------------------------------------------------
# Reporting — validate before trusting an hour
# ---------------------------------------------------------------------


def report_distribution(clusters: Sequence[Cluster], capped_hours: Sequence[float]) -> None:
    hours = list(capped_hours)
    print("\n  WORK SESSION DISTRIBUTION")
    if not hours:
        print("  no sessions — run ingestion first")
        return

    total = sum(hours)
    ordered = sorted(hours)
    p90 = ordered[int(0.9 * (len(ordered) - 1))]
    print(f"  sessions            {len(hours):,}")
    print(f"  median (hours)      {statistics.median(hours):.2f}")
    print(f"  mean (hours)        {statistics.mean(hours):.2f}")
    print(f"  p90 (hours)         {p90:.2f}")

    buckets = (0, 0.25, 0.5, 1, 2, 4, 8, float("inf"))
    labels = ("<15m", "15-30m", "30m-1h", "1-2h", "2-4h", "4-8h", "8h+")
    counts = [0] * len(labels)
    for h in hours:
        for i in range(len(buckets) - 1):
            if buckets[i] <= h < buckets[i + 1]:
                counts[i] += 1
                break
    peak = max(counts) or 1
    print("\n  histogram")
    for label, count in zip(labels, counts):
        bar = "#" * max(1, int(60 * count / peak)) if count else ""
        print(f"    {label:<8} {count:>7,}  {bar}")

    years = total / HOURS_PER_ENGINEER_YEAR
    print(f"\n  total inferred engineer-hours   {total:,.0f}")
    print(
        f"  implied engineer-years          {years:,.1f}  "
        f"(at {HOURS_PER_ENGINEER_YEAR}h/year)"
    )
    print(
        "  sanity check (Gate B): a year of Kafka+Flink should land in the low\n"
        "  hundreds of engineer-years, not single digits and not thousands. If\n"
        "  the median above is far outside 1-4 hours, the gap threshold or the\n"
        "  commit stream is wrong — fix it before computing a rupee on top."
    )


def report_gap_sensitivity(
    events: Sequence[RawEvent], lead_in_minutes: float, cap_hours: float
) -> None:
    print("\n  TOTAL INFERRED HOURS AT EACH GAP SETTING")
    for gap in GAP_SWEEP_MINUTES:
        clusters = cluster_events(events, gap, lead_in_minutes)
        capped = apply_daily_cap(clusters, cap_hours)
        print(
            f"    gap={gap:>3}min   sessions={len(clusters):>7,}   "
            f"hours={sum(capped):>10,.0f}"
        )


def report_spend_sensitivity(
    session: Session, events: Sequence[RawEvent], lead_in_minutes: float, cap_hours: float
) -> None:
    """Same sweep, priced. Needs actor.role_band (band_inference) and
    rate_card (rate_card --seed) — reports how many sessions it could not
    price rather than silently treating them as free."""
    actor_band = dict(session.execute(text("SELECT actor_hash, role_band FROM actor")).all())
    band_rate = dict(session.execute(text("SELECT role_band, hourly FROM rate_card")).all())

    print("\n  TOTAL INFERRED SPEND AT EACH GAP SETTING")
    if not band_rate:
        print("    rate_card is empty — seed it: python -m app.cost.rate_card --seed")
        return
    for gap in GAP_SWEEP_MINUTES:
        clusters = cluster_events(events, gap, lead_in_minutes)
        capped = apply_daily_cap(clusters, cap_hours)
        cost = Decimal(0)
        unpriced = 0
        for c, hours in zip(clusters, capped):
            rate = band_rate.get(actor_band.get(c.actor_hash))
            if rate is None:
                unpriced += 1
                continue
            cost += Decimal(str(hours)) * rate
        print(
            f"    gap={gap:>3}min   cost={cost:>14,.0f}   "
            f"unpriced_sessions={unpriced:,}"
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Infer work sessions from event_log.")
    parser.add_argument(
        "--dry-run", action="store_true", help="report only, write nothing"
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    gap = settings.session_gap_minutes
    lead_in = settings.session_lead_in_minutes
    cap = settings.session_daily_cap_hours

    with write_session() as session:
        events = load_events(session)
        if not events:
            print("no events — run ingestion first")
            return 1

        clusters, capped, rows = build_sessions(events, gap, lead_in, cap)

        if not args.dry_run:
            write_rows(session, rows)

        report_distribution(clusters, capped)
        report_gap_sensitivity(events, lead_in, cap)
        report_spend_sensitivity(session, events, lead_in, cap)

    verb = (
        "DRY RUN — nothing written"
        if args.dry_run
        else f"{len(rows):,} work_session rows written"
    )
    print(f"\n  {verb}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
