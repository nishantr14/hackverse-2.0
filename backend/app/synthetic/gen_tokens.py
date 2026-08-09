"""
Synthetic AI token usage generator.
Owner: Nishant (ingestion lane).
Phase: Tier 1.

    python -m app.synthetic.gen_tokens --dry-run
    python -m app.synthetic.gen_tokens

THIS DATA IS SYNTHETIC. The UI must badge it modelled, never observed. The
schema matches what a real vendor usage export would carry, so a real feed
can replace it without a schema change.

SEEDED, DETERMINISTIC, IDEMPOTENT
    Every row's id is a hash of what produced it, and every random draw comes
    from a generator seeded on that same id — not from a single stream walked
    in iteration order. Two people running this get byte-identical rows, and
    a re-run upserts in place rather than doubling the spend.

COST IS COMPUTED, NEVER SAMPLED
    cost = (tokens_in / 1e6 * input_price + tokens_out / 1e6 * output_price)
           * usd_to_inr
    Sampling a cost alongside the tokens would let the two drift, and the
    first person to divide one by the other on stage would find it.

NO ACTOR_HASH. NOT NOW, NOT LATER.
    `ai_usage` has no such column, this module never reads `actor`, and no
    view exposes token spend per person at any aggregation. Per-person AI
    usage is more sensitive than commit data, not less. A test enforces it.
"""

from __future__ import annotations

import argparse
import hashlib
import math
import random
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

import yaml
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import REPO_ROOT
from app.cost.rate_card import PLACEHOLDERS, is_placeholder
from app.db.models import AiUsage, CostEvent
from app.db.session import write_session

AI_RATES_PATH = REPO_ROOT / "config" / "ai_rates.yaml"
BASIS = "ai_tokens"
SOURCE = "synthetic"


class AiRatesError(RuntimeError):
    """Prices or the FX rate are unusable. Never invent either."""


@dataclass(frozen=True)
class Pricing:
    vendor: str
    input_per_million: Decimal
    output_per_million: Decimal
    fx: Decimal
    source: str


def load_config(path=None) -> dict:
    path = path or AI_RATES_PATH
    if not path.exists():
        raise AiRatesError(f"{path} is missing")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_pricing(cfg: dict) -> Pricing:
    """Validate before generating. The prices are the only real thing here."""
    p = cfg.get("pricing") or {}
    src = p.get("source") or {}
    missing = [
        f
        for f in ("publisher", "url", "retrieved")
        if not str(src.get(f) or "").strip() or is_placeholder(str(src.get(f)))
    ]
    if missing:
        raise AiRatesError(
            f"pricing.source is incomplete: {', '.join(missing)} in {AI_RATES_PATH}. "
            "The token counts are modelled; the prices they are multiplied by are "
            "not, and an uncited price makes a simulation look like an invoice."
        )
    fx = (p.get("usd_to_inr") or {}).get("rate")
    fx_source = str((p.get("usd_to_inr") or {}).get("source") or "").strip()
    if fx in (None, "") or not fx_source or fx_source.lower() in PLACEHOLDERS:
        raise AiRatesError(
            "pricing.usd_to_inr needs a rate AND a source. cost_event.cost has no "
            "currency column, so every figure in this database is INR — an "
            "unconverted USD token cost would be silently added to rupee labour "
            "cost and nothing would flag it."
        )
    for field in ("vendor", "input_per_million", "output_per_million"):
        if p.get(field) in (None, ""):
            raise AiRatesError(f"pricing.{field} is not set in {AI_RATES_PATH}")
    return Pricing(
        vendor=str(p["vendor"]),
        input_per_million=Decimal(str(p["input_per_million"])),
        output_per_million=Decimal(str(p["output_per_million"])),
        fx=Decimal(str(fx)),
        source=(
            f"{src['publisher']} — {src['url']} (retrieved {src['retrieved']}); "
            f"FX {fx} INR/USD: {fx_source}"
        ),
    )


def adoption_for(sprint: int, sprints: Sequence[int], cfg: dict) -> float:
    """Share of a sprint's work items that show any AI usage.

    Logistic, because tool adoption diffuses — flat, then steep, then
    saturating. A linear ramp makes the earliest sprints look like adoption
    had already started, which is the one thing the curve exists to deny.
    """
    a = cfg.get("adoption") or {}
    override = (a.get("by_sprint") or {}).get(sprint)
    if override is not None:
        return float(override)
    lo, hi = float(a.get("start", 0.0)), float(a.get("end", 0.5))
    if len(sprints) < 2:
        return hi
    position = (sprint - min(sprints)) / (max(sprints) - min(sprints))
    if str(a.get("shape", "logistic")) != "logistic":
        return lo + (hi - lo) * position
    k = float(a.get("steepness", 9.0))
    mid = float(a.get("midpoint", 0.65))
    return lo + (hi - lo) / (1.0 + math.exp(-k * (position - mid)))


def _rng(*parts: str, seed: int) -> random.Random:
    """A generator keyed on the row itself, not on iteration order.

    Walking one shared stream means inserting a work item upstream reshuffles
    every row after it, and 'deterministic' quietly stops being true.
    """
    digest = hashlib.sha256(f"{seed}|{'|'.join(parts)}".encode()).hexdigest()
    return random.Random(int(digest[:16], 16))


def usage_id_for(work_item_id: str, index: int) -> str:
    return hashlib.sha256(f"ai|{work_item_id}|{index}".encode()).hexdigest()[:24]


def price(tokens_in: int, tokens_out: int, pricing: Pricing) -> Decimal:
    million = Decimal(1_000_000)
    usd = (Decimal(tokens_in) / million) * pricing.input_per_million + (
        Decimal(tokens_out) / million
    ) * pricing.output_per_million
    return (usd * pricing.fx).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def load_work_items(session: Session) -> list[tuple[str, int, object]]:
    """Real work items only, with the sprint they sit in and when they closed."""
    return [
        (r[0], int(r[1]), r[2])
        for r in session.execute(
            text(
                """
                SELECT w.work_item_id, w.sprint,
                       COALESCE(w.closed_at, w.opened_at, e.last_ts) AS anchor
                  FROM work_item w
                  JOIN (SELECT work_item_id, MAX(ts) last_ts
                          FROM event_log GROUP BY 1) e USING (work_item_id)
                 WHERE w.sprint IS NOT NULL
                 ORDER BY w.work_item_id
                """
            )
        ).all()
    ]


def generate(session: Session, cfg: dict, pricing: Pricing):
    seed = int(cfg.get("seed", 0))
    tk = cfg.get("tokens") or {}
    items = load_work_items(session)
    sprints = sorted({s for _, s, _ in items})

    usage_rows, cost_rows = [], []
    per_sprint_cost: dict[int, Decimal] = defaultdict(lambda: Decimal(0))
    per_sprint_items: dict[int, int] = defaultdict(int)
    per_sprint_adopted: dict[int, int] = defaultdict(int)

    for work_item_id, sprint, anchor in items:
        per_sprint_items[sprint] += 1
        rate = adoption_for(sprint, sprints, cfg)
        rng = _rng(work_item_id, seed=seed)
        if rng.random() >= rate:
            continue
        per_sprint_adopted[sprint] += 1

        sessions = 1 + _poisson(rng, float(tk.get("sessions_lambda", 1.4)))
        for index in range(sessions):
            draw = _rng(work_item_id, str(index), seed=seed)
            tokens_in = max(
                1,
                int(
                    draw.lognormvariate(
                        math.log(float(tk.get("input_median", 12000))),
                        float(tk.get("input_sigma", 1.1)),
                    )
                ),
            )
            ratio = draw.lognormvariate(
                math.log(float(tk.get("output_ratio_median", 0.35))),
                float(tk.get("output_ratio_sigma", 0.5)),
            )
            tokens_out = max(1, int(tokens_in * min(ratio, 3.0)))
            cost = price(tokens_in, tokens_out, pricing)
            usage_id = usage_id_for(work_item_id, index)
            ts = anchor - timedelta(hours=draw.uniform(0, 72))

            usage_rows.append(
                {
                    "usage_id": usage_id,
                    "work_item_id": work_item_id,
                    "ts": ts,
                    "vendor": pricing.vendor,
                    "tokens_in": tokens_in,
                    "tokens_out": tokens_out,
                    "cost": cost,
                    "source": SOURCE,
                }
            )
            cost_rows.append(
                {
                    "cost_event_id": f"ai:{usage_id}",
                    "event_id": None,
                    "work_item_id": work_item_id,
                    # DELIBERATELY NULL. cost_event HAS an actor_hash column;
                    # AI spend must never populate it, or a per-person token
                    # figure becomes one GROUP BY away.
                    "actor_hash": None,
                    "hours": None,
                    "rate_band": None,
                    "cost": cost,
                    "basis": BASIS,
                }
            )
            per_sprint_cost[sprint] += cost

    return usage_rows, cost_rows, per_sprint_cost, per_sprint_items, per_sprint_adopted


def _poisson(rng: random.Random, lam: float) -> int:
    """Knuth. stdlib random has no Poisson and numpy is not a dependency."""
    target, count, product = math.exp(-lam), 0, 1.0
    while True:
        product *= rng.random()
        if product <= target:
            return count
        count += 1


def write(session: Session, usage_rows, cost_rows) -> None:
    for rows, model, key in (
        (usage_rows, AiUsage, "usage_id"),
        (cost_rows, CostEvent, "cost_event_id"),
    ):
        for start in range(0, len(rows), 1000):
            chunk = rows[start : start + 1000]
            if not chunk:
                continue
            stmt = insert(model).values(chunk)
            session.execute(
                stmt.on_conflict_do_update(
                    index_elements=[key],
                    set_={
                        c: getattr(stmt.excluded, c)
                        for c in chunk[0]
                        if c != key
                    },
                )
            )
    session.commit()


def report(per_cost, per_items, per_adopted, usage_rows, pricing) -> None:
    print("\n  SYNTHETIC AI TOKEN SPEND — modelled volumes, cited prices")
    print(f"  vendor  {pricing.vendor}")
    print(f"  prices  {pricing.source}")
    print(
        f"\n  {'sprint':>7} {'items':>8} {'with AI':>9} {'adoption':>9} "
        f"{'cost (INR)':>14}"
    )
    total = Decimal(0)
    for sprint in sorted(per_items):
        n, adopted = per_items[sprint], per_adopted.get(sprint, 0)
        cost = per_cost.get(sprint, Decimal(0))
        total += cost
        print(
            f"  {sprint:>7} {n:>8,} {adopted:>9,} "
            f"{(100.0 * adopted / n if n else 0):>8.1f}% {cost:>14,.0f}"
        )
    items = sum(per_items.values())
    adopted = sum(per_adopted.values())
    print(f"\n  total cost                {total:>14,.0f} INR")
    print(
        f"  work items with any AI    {adopted:,} of {items:,} "
        f"({100.0 * adopted / items if items else 0:.1f}%)"
    )
    print(f"  ai_usage rows             {len(usage_rows):,}")

    # The reconciliation the brief asks for, done here rather than asserted
    # in a comment: recompute every cost from its own tokens and compare.
    recomputed = sum(
        price(r["tokens_in"], r["tokens_out"], pricing) for r in usage_rows
    )
    stored = sum(r["cost"] for r in usage_rows)
    print(
        f"  cost reconciles           {'YES' if recomputed == stored else 'NO'} "
        f"(recomputed {recomputed:,.0f} vs stored {stored:,.0f})"
    )
    print(
        "\n  ai_usage has no actor_hash and this module never reads `actor`.\n"
        "  Per-person AI usage is more sensitive than commit data, not less.\n"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate synthetic AI usage.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    cfg = load_config()
    try:
        pricing = load_pricing(cfg)
    except AiRatesError as exc:
        print(f"\n  CANNOT GENERATE\n  {exc}\n")
        return 1

    with write_session() as session:
        usage, costs, per_cost, per_items, per_adopted = generate(
            session, cfg, pricing
        )
        if not args.dry_run:
            write(session, usage, costs)
    report(per_cost, per_items, per_adopted, usage, pricing)
    if args.dry_run:
        print("  (dry run — nothing written)\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
