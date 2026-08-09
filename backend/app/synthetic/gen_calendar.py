"""
Synthetic calendar / meeting time generator.
Owner: Nishant (ingestion lane).
Phase: Tier 1.

    python -m app.synthetic.gen_calendar --dry-run
    python -m app.synthetic.gen_calendar --meeting-hours 6

THIS DATA IS SYNTHETIC and driven by ONE visible assumption:
MEETING_HOURS_PER_WEEK, which the frontend exposes as a slider. Regenerating
with a different value moves every downstream number, so the slider is real
rather than decorative — that is the whole design, and the reconciliation
check at the end is what proves it.

WHAT IS AND IS NOT INVENTED
    WHO exists, WHEN they were working and WHAT component they were on are
    all real, read from event_log. Only the meetings between those events are
    modelled. Meetings are generated ONLY for actors with real events in that
    sprint, placed in the gaps between those events, and never overlapping
    one — an actor cannot be in a meeting at the moment they pushed a commit.

    Attendees are drawn from actors on the SAME component in the SAME sprint,
    so the attendance graph follows real collaboration instead of pairing
    people who have never touched the same code.

THE ABSENT COLUMNS ARE THE PRIVACY GUARANTEE
    `calendar_event` has no title, no description and no attendee list — only
    a duration, a count and a project link. Do not add them. Per-attendee
    participation lives in `event_log` as it does for every other activity,
    where the same k-anonymity floor already applies.
"""

from __future__ import annotations

import argparse
import hashlib
import random
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import CalendarEvent, EventLog
from app.db.session import write_session

SOURCE = "synthetic"
ACTIVITY = "meeting"

#: Fixed so two people regenerating get identical rows. Not in rates.yaml —
#: this generator has nothing to do with money.
SEED = 20260809

#: Meeting lengths people actually book, and how often. No 37-minute meetings.
DURATIONS_MIN: tuple[tuple[int, float], ...] = ((30, 0.45), (60, 0.40), (90, 0.15))

#: Most meetings are small. The occasional larger one is a planning or
#: incident call, and it matters because cost scales with attendee count.
SIZES: tuple[tuple[int, float], ...] = (
    (2, 0.28), (3, 0.24), (4, 0.18), (5, 0.12), (6, 0.08), (9, 0.07), (14, 0.03)
)

#: A meeting needs this much clear space between two real events to fit.
MIN_GAP_MIN = 45

#: Working hours, local-naive, so meetings do not land at 3am and make the
#: session inference downstream look absurd.
WORK_START_H, WORK_END_H = 9, 18


@dataclass
class Meeting:
    meeting_id: str
    ts: datetime
    duration_min: int
    attendees: list[str]
    work_item_id: str | None
    project: str
    component: str | None
    sprint: int


def _rng(*parts: str) -> random.Random:
    digest = hashlib.sha256(f"{SEED}|{'|'.join(parts)}".encode()).hexdigest()
    return random.Random(int(digest[:16], 16))


def _weighted(rng: random.Random, options: Sequence[tuple[int, float]]) -> int:
    roll, cumulative = rng.random(), 0.0
    for value, weight in options:
        cumulative += weight
        if roll <= cumulative:
            return value
    return options[-1][0]


def load_activity(session: Session) -> dict:
    """Real events, per sprint and component. The scaffolding meetings hang on."""
    rows = session.execute(
        text(
            """
            SELECT e.sprint, e.repo, COALESCE(e.component, '(none)') AS component,
                   e.resource AS actor_hash, e.ts, e.case_id
              FROM v_event_log e
             WHERE e.resource IS NOT NULL AND e.sprint IS NOT NULL
               AND e.activity <> 'meeting'
             ORDER BY e.sprint, e.repo, component, e.resource, e.ts
            """
        )
    ).all()
    activity: dict = defaultdict(lambda: defaultdict(list))
    for sprint, repo, component, actor, ts, case_id in rows:
        activity[(int(sprint), repo, component)][actor].append((ts, case_id))
    return activity


def free_slot(
    rng: random.Random, events: list[tuple[datetime, str]], duration_min: int
) -> tuple[datetime, str | None] | None:
    """A gap between two real events big enough to hold the meeting.

    Returns the start and the case the surrounding work belonged to, so the
    meeting attaches to a work item the attendee was demonstrably on rather
    than to one picked at random.
    """
    if len(events) < 2:
        return None
    order = list(range(len(events) - 1))
    rng.shuffle(order)
    for i in order:
        (start, case), (end, _) = events[i], events[i + 1]
        gap_min = (end - start).total_seconds() / 60.0
        if gap_min < duration_min + MIN_GAP_MIN:
            continue
        latest = gap_min - duration_min - MIN_GAP_MIN / 2
        offset = rng.uniform(MIN_GAP_MIN / 2, max(MIN_GAP_MIN / 2, latest))
        when = start + timedelta(minutes=offset)
        if not WORK_START_H <= when.hour < WORK_END_H:
            continue
        return when, case
    return None


def generate(session: Session, meeting_hours_per_week: float, sprint_days: int):
    activity = load_activity(session)
    weeks = sprint_days / 7.0
    meetings: list[Meeting] = []
    hours_by_actor_sprint: dict[tuple[str, int], float] = defaultdict(float)

    # THE BUDGET IS PER ACTOR PER SPRINT, NOT PER COMPONENT.
    #
    # Budgeting inside each (sprint, repo, component) group overshoots by
    # exactly as much as people work across components: an actor touching
    # three components got three separate allocations and ended up with three
    # times the meeting load. Measured at +52% against a 4 h/week assumption
    # before this was a single global quota. The reconciliation check below is
    # what caught it, which is the reason it asserts rather than reports.
    target_hours = meeting_hours_per_week * weeks
    remaining: dict[tuple[str, int], float] = defaultdict(lambda: target_hours)

    for (sprint, repo, component), actors in sorted(activity.items()):
        eligible = [a for a, evs in actors.items() if len(evs) >= 2]
        if len(eligible) < 2:
            continue
        rng = _rng(str(sprint), repo, component)
        attempts = 0

        while attempts < 400:
            attempts += 1
            # Sample a natural length, then fall back to shorter ones if too
            # few people can still afford it. Without the fallback the budget
            # is left stranded in chunks smaller than the meeting that would
            # spend it: at 2 h/week the leftovers alone were 8% of the total,
            # and the drift got worse the lower the slider went — exactly
            # backwards, since a small assumption is the one people test with.
            options = sorted({_weighted(rng, DURATIONS_MIN)} | {d for d, _ in DURATIONS_MIN})
            for duration in sorted(options, key=lambda d: -d):
                hours = duration / 60.0
                # Only people who still owe at least this meeting's worth. An
                # attendee is never pushed past the assumption, so the total
                # can undershoot but never inflate.
                candidates = [a for a in eligible if remaining[(a, sprint)] >= hours]
                if len(candidates) >= 2:
                    break
            else:
                break
            if len(candidates) < 2:
                break
            size = min(_weighted(rng, SIZES), len(candidates))
            organiser = candidates[rng.randrange(len(candidates))]
            slot = free_slot(rng, actors[organiser], duration)
            if slot is None:
                continue
            when, case_id = slot
            others = [a for a in candidates if a != organiser]
            rng.shuffle(others)
            attendees = [organiser, *others[: size - 1]]

            meeting_id = hashlib.sha256(
                f"mtg|{sprint}|{repo}|{component}|{len(meetings)}".encode()
            ).hexdigest()[:24]
            meetings.append(
                Meeting(
                    meeting_id=meeting_id,
                    ts=when,
                    duration_min=duration,
                    attendees=attendees,
                    work_item_id=case_id,
                    project=repo,
                    component=None if component == "(none)" else component,
                    sprint=sprint,
                )
            )
            for actor in attendees:
                hours_by_actor_sprint[(actor, sprint)] += hours
                remaining[(actor, sprint)] -= hours

    return meetings, hours_by_actor_sprint


def to_rows(meetings: Sequence[Meeting]):
    calendar_rows, event_rows = [], []
    for m in meetings:
        calendar_rows.append(
            {
                "meeting_id": m.meeting_id,
                "work_item_id": m.work_item_id,
                "project": m.project,
                "ts": m.ts,
                "duration_min": m.duration_min,
                "attendee_count": len(m.attendees),
                "source": SOURCE,
                # No title. No description. No attendee list. The absence of
                # those three columns IS the privacy guarantee.
            }
        )
        for actor in m.attendees:
            if m.work_item_id is None:
                # event_log.work_item_id is NOT NULL in the frozen schema, so
                # a project-level meeting cannot become an event. It stays in
                # calendar_event and is counted there rather than dropped.
                continue
            event_rows.append(
                {
                    "event_id": hashlib.sha256(
                        f"mtg|{m.meeting_id}|{actor}".encode()
                    ).hexdigest()[:24],
                    "work_item_id": m.work_item_id,
                    "actor_hash": actor,
                    "activity": ACTIVITY,
                    "ts": m.ts,
                    "duration_s": m.duration_min * 60,
                    "source": SOURCE,
                    "attrs": {
                        "ingest_source": SOURCE,
                        "meeting_id": m.meeting_id,
                        "attendee_count": len(m.attendees),
                        "assumption": "meeting_hours_per_week",
                    },
                }
            )
    return calendar_rows, event_rows


def write(session: Session, calendar_rows, event_rows) -> None:
    # Regenerating with a different slider value must REPLACE the previous
    # answer, not add to it. Synthetic rows are the only ones deleted, and
    # only ever by source.
    session.execute(
        text("DELETE FROM event_log WHERE source = 'synthetic' AND activity = :a"),
        {"a": ACTIVITY},
    )
    session.execute(text("DELETE FROM calendar_event WHERE source = 'synthetic'"))
    for rows, model, key in (
        (calendar_rows, CalendarEvent, "meeting_id"),
        (event_rows, EventLog, "event_id"),
    ):
        for start in range(0, len(rows), 1000):
            chunk = rows[start : start + 1000]
            if not chunk:
                continue
            stmt = insert(model).values(chunk)
            session.execute(stmt.on_conflict_do_nothing(index_elements=[key]))
    session.commit()


def report(
    session: Session,
    meetings: Sequence[Meeting],
    hours: dict,
    target_per_week: float,
    sprint_days: int,
    wrote: bool,
) -> bool:
    weeks = sprint_days / 7.0
    total_hours = sum(m.duration_min / 60.0 * len(m.attendees) for m in meetings)
    per_week = [h / weeks for h in hours.values()]
    mean = sum(per_week) / len(per_week) if per_week else 0.0
    drift = (mean - target_per_week) / target_per_week * 100 if target_per_week else 0

    print("\n  SYNTHETIC MEETINGS — one assumption, exposed as a slider")
    print(f"  MEETING_HOURS_PER_WEEK    {target_per_week}")
    print(f"  meetings generated        {len(meetings):,}")
    print(f"  attendee-hours            {total_hours:,.0f}")
    print(f"  actors with meetings      {len({a for a, _ in hours}):,}")
    print(
        f"\n  RECONCILIATION  mean {mean:.2f} h/actor/week vs assumed "
        f"{target_per_week:.2f}  ({drift:+.1f}%)"
    )
    ok = abs(drift) <= 5.0
    print(f"  within 5%                 {'YES' if ok else 'NO — investigate'}")

    sizes = defaultdict(int)
    for m in meetings:
        sizes[len(m.attendees)] += 1
    print("\n  attendee-count distribution")
    for size in sorted(sizes):
        print(f"    {size:>3} attendees  {sizes[size]:>6,}")

    if wrote:
        rows = session.execute(
            text(
                "SELECT source, count(*) FROM event_log GROUP BY 1 ORDER BY 2 DESC"
            )
        ).all()
        total = sum(n for _, n in rows)
        print("\n  event_log observed vs synthetic")
        for source, n in rows:
            print(f"    {source:<12} {n:>9,}  {100.0 * n / total:>5.1f}%")
        print(
            "\n  The synthetic share is what the UI must badge. A meeting is an\n"
            "  assumption with a number attached, never an observation.\n"
        )
    return ok


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate synthetic meetings.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--meeting-hours",
        type=float,
        default=None,
        help="override MEETING_HOURS_PER_WEEK for this run",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    hours_per_week = (
        args.meeting_hours
        if args.meeting_hours is not None
        else settings.meeting_hours_per_week
    )

    with write_session() as session:
        meetings, hours = generate(session, hours_per_week, settings.sprint_days)
        if not meetings:
            print("no eligible actors — run ingestion and the normaliser first")
            return 1
        calendar_rows, event_rows = to_rows(meetings)
        if not args.dry_run:
            write(session, calendar_rows, event_rows)
        ok = report(
            session, meetings, hours, hours_per_week, settings.sprint_days,
            wrote=not args.dry_run,
        )
    if args.dry_run:
        print("  (dry run — nothing written)\n")
    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
