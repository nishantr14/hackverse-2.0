"""
Band inference — a stated rule over contribution history, labelled inferred.
Owner: Diljit (cost lane).
Phase: Tier 1.

    python -m app.cost.band_inference --dry-run   # decide thresholds
    python -m app.cost.band_inference             # write actor.role_band

THIS IS NOT THE RATE CARD AND MUST NOT BE CONFUSED WITH IT.

`rate_card` holds public figures with a URL on screen. This module decides
which of those four buckets an actor falls into, from nothing but the events
they left behind. GitHub does not publish seniority; there is no ground truth
here and there never will be. So every row it writes carries
`band_basis = 'inferred'`, the UI must read "inferred band" rather than
"band", and the schema's CHECK allows only 'inferred' or 'stated' precisely so
that a later shortcut cannot quietly promote a guess into a fact.

    tenure_months  first event to last event, for that actor
    merged         distinct work items where the actor AUTHORED the merged commit
    reviews        reviews, approvals and change-requests they gave

Thresholds live in config/rates.yaml, not here. `--dry-run` prints what the
configured ones produce alongside the actor count at every candidate value, so
the choice is made by a person looking at numbers rather than by whoever last
edited this file.
"""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from app.cost.rate_card import BANDS, load_config
from app.db.session import write_session

#: Every band ever written is inferred. The schema also allows 'stated'; no
#: code path in this repository may produce it, because nothing in our data
#: says what anyone's actual role is.
BAND_BASIS = "inferred"

#: actor.tenure_bucket's CHECK constraint, in months.
TENURE_BUCKETS: tuple[tuple[float, str], ...] = ((24.0, "gt_2y"), (6.0, "6m_2y"))
TENURE_FALLBACK = "lt_6m"

#: Average seconds in a Gregorian month. Used for tenure only, where a day
#: either way changes nothing and a calendar-accurate diff costs clarity.
SECONDS_PER_MONTH = 2_629_746.0

#: Candidate values printed in the sensitivity table. Not applied.
SWEEP = {
    "tenure_months": (3, 6, 9, 12, 18, 24, 36),
    "reviews": (5, 10, 25, 50, 100, 200),
    "merged": (1, 5, 10, 20, 30, 50),
}

FEATURE_SQL = """
WITH span AS (
    SELECT actor_hash,
           MIN(ts) AS first_ts,
           MAX(ts) AS last_ts,
           COUNT(*) AS n_events,
           BOOL_OR(COALESCE(attrs->>'ingest_source', 'git_local') = 'asf_jira')
             AS seen_in_jira
      FROM event_log
     WHERE actor_hash IS NOT NULL
     GROUP BY 1
), reviewed AS (
    SELECT actor_hash, COUNT(*) AS reviews
      FROM event_log
     WHERE actor_hash IS NOT NULL
       AND activity IN ('review', 'approved', 'changes_requested')
     GROUP BY 1
), authored AS (
    -- The AUTHOR of the merged commit, not whoever pressed merge. On apache a
    -- committer merges other people's pull requests, so event_log.actor_hash
    -- on a `merged` event is the committer; the author is in attrs.
    SELECT attrs->>'authored_by' AS actor_hash,
           COUNT(DISTINCT work_item_id) AS merged
      FROM event_log
     WHERE activity = 'merged' AND attrs->>'authored_by' IS NOT NULL
     GROUP BY 1
), committed AS (
    -- Diagnostic only: how often they were the one who merged. Committer
    -- rights on an Apache project are earned and are arguably the strongest
    -- seniority signal in the data, so it is printed but never applied.
    SELECT actor_hash, COUNT(*) AS merges_performed
      FROM event_log WHERE activity = 'merged' AND actor_hash IS NOT NULL
     GROUP BY 1
)
SELECT a.actor_hash,
       COALESCE(EXTRACT(EPOCH FROM (s.last_ts - s.first_ts)), 0) / :spm
         AS tenure_months,
       COALESCE(r.reviews, 0)          AS reviews,
       COALESCE(m.merged, 0)           AS merged,
       COALESCE(c.merges_performed, 0) AS merges_performed,
       COALESCE(s.n_events, 0)         AS n_events,
       COALESCE(s.seen_in_jira, FALSE) AS seen_in_jira,
       s.first_ts
  FROM actor a
  LEFT JOIN span     s ON s.actor_hash = a.actor_hash
  LEFT JOIN reviewed r ON r.actor_hash = a.actor_hash
  LEFT JOIN authored m ON m.actor_hash = a.actor_hash
  LEFT JOIN committed c ON c.actor_hash = a.actor_hash
"""


@dataclass(frozen=True)
class ActorFeatures:
    actor_hash: str
    tenure_months: float
    reviews: int
    merged: int
    merges_performed: int
    n_events: int
    seen_in_jira: bool
    first_ts: Any


def load_features(session: Session) -> list[ActorFeatures]:
    rows = session.execute(text(FEATURE_SQL), {"spm": SECONDS_PER_MONTH}).all()
    return [
        ActorFeatures(
            actor_hash=r[0],
            tenure_months=float(r[1] or 0),
            reviews=int(r[2]),
            merged=int(r[3]),
            merges_performed=int(r[4]),
            n_events=int(r[5]),
            seen_in_jira=bool(r[6]),
            first_ts=r[7],
        )
        for r in rows
    ]


def load_thresholds(cfg: dict[str, Any] | None = None) -> dict[str, dict[str, float]]:
    cfg = cfg or load_config()
    thresholds = ((cfg.get("band_inference") or {}).get("thresholds")) or {}
    if not thresholds:
        raise ValueError("config/rates.yaml has no band_inference.thresholds")
    unknown = set(thresholds) - set(BANDS)
    if unknown:
        raise ValueError(f"unknown band(s) in thresholds: {sorted(unknown)}")
    if "junior" in thresholds:
        raise ValueError("junior is the fallthrough and takes no conditions")
    return {band: dict(conditions) for band, conditions in thresholds.items()}


def assign_band(
    features: ActorFeatures, thresholds: dict[str, dict[str, float]]
) -> str:
    """First band, most senior first, whose every condition is met."""
    for band in reversed(BANDS):  # staff, senior, mid
        conditions = thresholds.get(band)
        if not conditions:
            continue
        if all(
            getattr(features, name, 0) >= floor for name, floor in conditions.items()
        ):
            return band
    return "junior"


def tenure_bucket(months: float) -> str:
    for floor, bucket in TENURE_BUCKETS:
        if months >= floor:
            return bucket
    return TENURE_FALLBACK


def apply_bands(
    session: Session, features: Sequence[ActorFeatures], thresholds
) -> Counter:
    rows, bands = [], Counter()
    for f in features:
        band = assign_band(f, thresholds)
        bands[band] += 1
        rows.append(
            {
                "hash": f.actor_hash,
                "band": band,
                "bucket": tenure_bucket(f.tenure_months),
            }
        )
    if rows:
        session.execute(
            text(
                "UPDATE actor SET role_band = :band, tenure_bucket = :bucket, "
                # Not parameterised, and not a variable anywhere. There is no
                # code path in this repository that writes 'stated'.
                "band_basis = 'inferred' WHERE actor_hash = :hash"
            ).bindparams(bindparam("hash"), bindparam("band"), bindparam("bucket")),
            rows,
        )
        session.commit()
    return bands


# ---------------------------------------------------------------------
# Reporting — the point of the exercise
# ---------------------------------------------------------------------


def print_distribution(bands: Counter, cfg: dict[str, Any], total: int) -> None:
    target = ((cfg.get("band_inference") or {}).get("target_distribution")) or {}
    print("\n  INFERRED BAND DISTRIBUTION")
    print(f"  {'band':<8} {'actors':>8} {'share':>8} {'target':>8} {'gap':>8}")
    for band in reversed(BANDS):
        n = bands.get(band, 0)
        share = 100.0 * n / total if total else 0.0
        want = float(target.get(band, 0))
        print(
            f"  {band:<8} {n:>8,} {share:>7.1f}% {want:>7.0f}% "
            f"{share - want:>+7.1f}"
        )
    print(f"  {'total':<8} {total:>8,}")


def print_sensitivity(features: Sequence[ActorFeatures]) -> None:
    """How many actors clear each candidate value, one knob at a time.

    Deliberately NOT a search for the thresholds that hit the target. Fitting
    the rule to a desired answer is how an inference becomes a fiction, and
    the shape of the underlying distribution is the thing worth looking at
    anyway.
    """
    print("\n  ACTORS CLEARING EACH CANDIDATE THRESHOLD (nothing applied)")
    for name, values in SWEEP.items():
        counts = [
            sum(1 for f in features if getattr(f, name, 0) >= v) for v in values
        ]
        print(f"\n  {name}")
        print("    " + "".join(f"{v:>10}" for v in values))
        print("    " + "".join(f"{c:>10,}" for c in counts))


def print_caveats(features: Sequence[ActorFeatures]) -> None:
    """The two things that will make this distribution look wrong, measured."""
    total = len(features)
    silent = sum(1 for f in features if f.n_events == 0)
    jira = [f for f in features if f.seen_in_jira]
    git_only = [f for f in features if not f.seen_in_jira]

    def clears(group, months):
        return sum(1 for f in group if f.tenure_months >= months)

    print("\n  READ THIS BEFORE CHANGING A THRESHOLD")

    print(
        f"\n  1. TENURE IS NOT MEASURED OVER THE SAME WINDOW FOR EVERYONE.\n"
        f"     git and PR history are bounded by HISTORY_MONTHS, but Jira\n"
        f"     changelogs were deliberately kept back past the window so case\n"
        f"     histories reconstruct — the oldest event in the log is from 2012.\n"
        f"     So an actor visible in Jira can show years of tenure while an\n"
        f"     equally senior contributor who never touches Jira is capped:\n"
        f"       seen in Jira      {len(jira):>5} actors, "
        f"{clears(jira, 24):>4} clear 24 months "
        f"({100.0 * clears(jira, 24) / max(len(jira), 1):.0f}%)\n"
        f"       never in Jira     {len(git_only):>5} actors, "
        f"{clears(git_only, 24):>4} clear 24 months "
        f"({100.0 * clears(git_only, 24) / max(len(git_only), 1):.0f}%)\n"
        f"     Raising the tenure floor sharpens that bias rather than fixing it."
    )

    print(
        f"\n  2. {silent:,} of {total:,} actors have NO events at all "
        f"({100.0 * silent / max(total, 1):.0f}%).\n"
        "     They exist because they authored a Jira changelog entry on a\n"
        "     field we do not model as an event. They are real people who did\n"
        "     real work, but every feature here reads zero for them, so they\n"
        "     all land in junior and drag the share up on their own."
    )

    print(
        "\n  3. Apache has a long tail of one-commit drive-by contributors, and\n"
        "     they are genuinely junior BY THIS RULE — the rule measures\n"
        "     contribution to THESE repositories, not a person's career. A\n"
        "     principal engineer who sent one patch is junior here. That is a\n"
        "     defensible thing to say out loud and an indefensible thing to\n"
        "     hide by moving a threshold."
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Infer actor bands.")
    parser.add_argument(
        "--dry-run", action="store_true", help="report only, write nothing"
    )
    args = parser.parse_args(argv)

    cfg = load_config()
    thresholds = load_thresholds(cfg)

    with write_session() as session:
        features = load_features(session)
        if not features:
            print("no actors — run ingestion first")
            return 1

        if args.dry_run:
            bands = Counter(assign_band(f, thresholds) for f in features)
        else:
            bands = apply_bands(session, features, thresholds)

    print("\n  thresholds (from config/rates.yaml, not from code)")
    for band in reversed(BANDS):
        if band in thresholds:
            conditions = ", ".join(
                f"{k} >= {v}" for k, v in thresholds[band].items()
            )
            print(f"    {band:<8} {conditions}")
    print(f"    {'junior':<8} everything else")

    print_distribution(bands, cfg, len(features))
    print_sensitivity(features)
    print_caveats(features)

    print(
        "\n  Every row written carries band_basis = 'inferred'. The UI must say\n"
        "  \"inferred band\". Nothing in this repository writes 'stated'.\n"
    )
    print(
        "  STOPPING HERE. The thresholds above are the ones in config/rates.yaml\n"
        "  and have not been adjusted to hit the target. Pick the values you can\n"
        "  defend, put them in that file, and re-run.\n"
        if args.dry_run
        else "  Bands written. Re-run with --dry-run to explore other thresholds.\n"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
