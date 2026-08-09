"""
Variants — ranked by cost share, not frequency.
Owner: Diljit (waste lane).
Phase: Tier 0 (feeds ProcessView).

    python -m app.waste.variants

v_variants (migrations/004_process_and_waste_views.sql) groups cases by
their full collapsed, non-CI activity sequence, sums cost, and computes
cost share per repo. RANK BY COST SHARE: the finding worth a demo beat is a
variant rare by case count and large by cost share, and the API must be
able to return exactly that ordering — this module's default is that
ordering, not the modal path.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

VARIANTS_QUERY = """
    SELECT variant_id, repo, activity_sequence, n_cases, total_cost,
           cost_share_pct, is_modal
      FROM v_variants
"""


@dataclass(frozen=True)
class Variant:
    variant_id: str
    repo: str
    activity_sequence: list[str]
    n_cases: int
    total_cost: float
    cost_share_pct: float
    is_modal: bool


def load_variants(session: Session, repo: str | None = None) -> list[Variant]:
    """Cost-share-ranked by default — pass repo to scope to one project."""
    query = VARIANTS_QUERY + (" WHERE repo = :repo" if repo else "")
    rows = session.execute(text(query), {"repo": repo} if repo else {}).all()
    variants = [
        Variant(
            variant_id=r[0],
            repo=r[1],
            activity_sequence=list(r[2]),
            n_cases=int(r[3]),
            total_cost=float(r[4]),
            cost_share_pct=float(r[5]) if r[5] is not None else 0.0,
            is_modal=bool(r[6]),
        )
        for r in rows
    ]
    return sorted(variants, key=lambda v: v.cost_share_pct, reverse=True)


def rare_but_costly(variants: list[Variant], case_count_ceiling: int = 5) -> list[Variant]:
    """Rare by case count, large by cost share — the finding worth staging."""
    return [v for v in variants if v.n_cases <= case_count_ceiling and v.cost_share_pct > 0][:10]


def main() -> int:
    from app.db.session import write_session

    with write_session() as session:
        variants = load_variants(session)
    modal = next((v for v in variants if v.is_modal), None)
    print(f"\n  VARIANTS — {len(variants)} distinct (collapsed) sequences")
    if modal:
        print(
            f"    modal (happy path): {modal.repo} n_cases={modal.n_cases:,} "
            f"cost_share={modal.cost_share_pct:.1f}%"
        )
    print("\n    top 5 by cost share:")
    for v in variants[:5]:
        print(
            f"      {v.repo:<15} n_cases={v.n_cases:>6,} "
            f"cost={v.total_cost:>12,.0f} share={v.cost_share_pct:>5.1f}% "
            f"{'(modal)' if v.is_modal else ''}"
        )
    print("\n    rare but costly (n_cases<=5, sorted by cost share):")
    for v in rare_but_costly(variants):
        print(f"      {v.repo:<15} n_cases={v.n_cases:>3} cost={v.total_cost:>10,.0f} share={v.cost_share_pct:.2f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
