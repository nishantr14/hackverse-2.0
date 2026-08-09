"""
Rate card — public comp-band medians, cited.
Owner: Diljit (cost lane).
Phase: Tier 1.

    python -m app.cost.rate_card            # print the card
    python -m app.cost.rate_card --seed     # write it to rate_card

This module knows nothing about which band anyone is in. That is
`band_inference`, and the separation is deliberate: decision #8 says rates are
PUBLIC AND CITED while band assignment is INFERRED AND LABELLED, and the
fastest way to lose that distinction is to compute both in one file.

    hourly = (annual * loading) / (working_days * hours_per_day)

Every figure comes from `config/rates.yaml` so the source can be swapped
without touching code. There is no default annual salary anywhere in here —
a plausible-looking number with no citation behind it is the single worst
thing this table could contain, so an unset band is an error, not a fallback.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import REPO_ROOT
from app.db.models import RateCard
from app.db.session import write_session

RATES_PATH = REPO_ROOT / "config" / "rates.yaml"

#: The four bands, in ascending seniority. Matches actor.role_band's CHECK
#: constraint; the schema is frozen, so this list is not ours to extend.
BANDS: tuple[str, ...] = ("junior", "mid", "senior", "staff")

#: Placeholder text that must never reach the screen. A source string reading
#: "TODO" is worse than a blank one — it looks like a citation from six feet
#: away, which is the distance a judge is standing at.
PLACEHOLDERS = ("todo", "tbd", "fill", "xxx", "changeme", "change-me", "none")


def is_placeholder(value: str) -> bool:
    """Exact match, or the first word of one — "FILL ME" and "TODO: get url"
    are placeholders too, and an exact-match check waves both through.

    Matched on the FIRST WORD rather than as a substring, so a genuine URL
    that happens to contain one of these words is not rejected.
    """
    cleaned = value.strip().lower()
    if cleaned in PLACEHOLDERS:
        return True
    first = re.split(r"[^a-z-]+", cleaned, maxsplit=1)[0]
    return first in PLACEHOLDERS


class RateCardError(RuntimeError):
    """The rate card is unusable. Never fall back to a made-up number."""


@dataclass(frozen=True)
class Rate:
    role_band: str
    annual: Decimal
    hourly: Decimal
    currency: str
    loading: Decimal
    source: str


def load_config(path: Path | None = None) -> dict[str, Any]:
    path = path or RATES_PATH
    if not path.exists():
        raise RateCardError(
            f"{path} is missing. It holds the public rate figures and their "
            "citation; there is no built-in default to fall back to."
        )
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if "rate_card" not in data:
        raise RateCardError(f"{path} has no `rate_card:` section")
    return data


def citation(cfg: dict[str, Any]) -> str:
    """Build the string the UI renders next to every rupee figure.

    Fails loudly and by name. This is the check that stops a demo from
    showing a number with nothing behind it, so it does not warn, it does not
    substitute a placeholder, and it does not proceed.
    """
    source = (cfg.get("rate_card") or {}).get("source") or {}
    missing = [
        field
        for field in ("publisher", "url", "retrieved")
        if not str(source.get(field) or "").strip()
        or is_placeholder(str(source.get(field)))
    ]
    if missing:
        raise RateCardError(
            f"rate_card.source is incomplete: {', '.join(missing)} not set in "
            f"{RATES_PATH}.\nThe citation renders on screen beside the money. "
            "An uncited rate is an invented rate — fill these in before seeding."
        )
    retrieved = str(source["retrieved"]).strip()
    try:
        date.fromisoformat(retrieved)
    except ValueError as exc:
        raise RateCardError(
            f"rate_card.source.retrieved must be YYYY-MM-DD, got {retrieved!r}"
        ) from exc
    return f"{source['publisher']} — {source['url']} (retrieved {retrieved})"


def hourly_from_annual(
    annual: Decimal, loading: Decimal, working_days: int, hours_per_day: int
) -> Decimal:
    if working_days <= 0 or hours_per_day <= 0:
        raise RateCardError("working_days and hours_per_day must both be > 0")
    hours = Decimal(working_days) * Decimal(hours_per_day)
    return ((annual * loading) / hours).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def build_rates(cfg: dict[str, Any] | None = None) -> list[Rate]:
    cfg = cfg or load_config()
    card = cfg["rate_card"]
    note = citation(cfg)  # raises before any number is computed

    loading = Decimal(str(card.get("loading", "1.30")))
    if loading <= 0:
        raise RateCardError("loading must be > 0")
    working_days = int(card.get("working_days", 0))
    hours_per_day = int(card.get("hours_per_day", 0))
    currency = str(card.get("currency") or "INR")
    bands = card.get("bands") or {}

    unset = [b for b in BANDS if (bands.get(b) or {}).get("annual") in (None, "")]
    if unset:
        raise RateCardError(
            f"no annual figure for: {', '.join(unset)} in {RATES_PATH}. "
            "Every band needs one from the cited source — there is no default."
        )

    rates = []
    for band in BANDS:
        annual = Decimal(str(bands[band]["annual"]))
        if annual <= 0:
            raise RateCardError(f"{band}.annual must be > 0, got {annual}")
        rates.append(
            Rate(
                role_band=band,
                annual=annual,
                hourly=hourly_from_annual(
                    annual, loading, working_days, hours_per_day
                ),
                currency=currency,
                loading=loading,
                source=note,
            )
        )

    ordered = [r.annual for r in rates]
    if ordered != sorted(ordered):
        raise RateCardError(
            f"annual figures are not ascending across {BANDS}: {ordered}. "
            "Either the bands are mislabelled or two were swapped."
        )
    return rates


def seed(session: Session, rates: Sequence[Rate] | None = None) -> int:
    """Upsert the card. Idempotent — re-running changes no counts."""
    rates = rates or build_rates()
    stmt = insert(RateCard).values(
        [
            {
                "role_band": r.role_band,
                "hourly": r.hourly,
                "currency": r.currency,
                "loading": r.loading,
                "source": r.source,
            }
            for r in rates
        ]
    )
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["role_band"],
            set_={
                "hourly": stmt.excluded.hourly,
                "currency": stmt.excluded.currency,
                "loading": stmt.excluded.loading,
                "source": stmt.excluded.source,
            },
        )
    )
    session.commit()
    return len(rates)


def print_card(rates: Sequence[Rate]) -> None:
    print("\n  RATE CARD — public, cited, not inferred")
    print(f"  {'band':<8} {'annual':>14} {'loaded':>14} {'hourly':>12}")
    for r in rates:
        loaded = (r.annual * r.loading).quantize(Decimal(1))
        print(
            f"  {r.role_band:<8} {r.currency} {r.annual:>10,.0f} "
            f"{r.currency} {loaded:>10,.0f} {r.currency} {r.hourly:>8,.2f}"
        )
    print(f"\n  loading   x{rates[0].loading}  (fully loaded — say so on the slide)")
    print(f"  source    {rates[0].source}")
    print(
        "\n  Band ASSIGNMENT is a separate, inferred thing. See "
        "app/cost/band_inference.py."
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build and seed the rate card.")
    parser.add_argument("--seed", action="store_true", help="write to rate_card")
    args = parser.parse_args(argv)

    try:
        rates = build_rates()
    except RateCardError as exc:
        print(f"\n  RATE CARD NOT USABLE\n  {exc}\n", file=sys.stderr)
        return 1

    print_card(rates)
    if args.seed:
        with write_session() as session:
            print(f"\n  seeded {seed(session, rates)} rate_card rows")
    else:
        print("\n  (dry run — pass --seed to write)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
