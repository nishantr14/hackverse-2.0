"""
Waste router — rework, review latency, CI waste, backlog time.
Owner: Diljit (waste lane). Reads from app/waste/*, never computes waste itself.
Phase: Tier 1.

key_person is deliberately absent from this router. It reads
v_actor_component_activity, which the frozen schema's own comment says
must never be granted to the app role or exposed through a route — it is
per-actor by construction, and the k-anonymity floor this module applies
happens in Python, not in a database grant. It stays CLI/batch-only:
`python -m app.waste.key_person`.
"""

from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.cost.cost_attribution import load_config
from app.db.session import get_read_session
from app.waste import backlog, ci_waste, review_latency, rework

router = APIRouter(prefix="/waste", tags=["waste"])


def _finding(f) -> dict:
    return {
        "detector": f.detector,
        "hours": f.hours,
        "cost": f.cost,
        "unitNote": f.unit_note,
        "evidenceQuery": f.evidence_query,
        "costPending": f.cost_pending,
    }


@router.get("/summary")
def get_summary(session: Session = Depends(get_read_session)):
    """One card per detector, ranked highest-cost-first (unpriced findings
    last — a null cost is not a small cost)."""
    findings = [
        ci_waste.detect(session),
        rework.detect(session),
        *review_latency.detect(session),
    ]
    backlog_finding, backlog_segments = backlog.detect(session)
    findings.append(backlog_finding)

    ranked = sorted(findings, key=lambda f: (f.cost is None, -(f.cost or 0)))
    return {
        "findings": [_finding(f) for f in ranked],
        "backlogSegments": [asdict(s) for s in backlog_segments],
    }


@router.get("/ci")
def get_ci_waste(session: Session = Depends(get_read_session)):
    return _finding(ci_waste.detect(session))


@router.get("/rework")
def get_rework(session: Session = Depends(get_read_session)):
    return _finding(rework.detect(session))


@router.get("/review-latency")
def get_review_latency(session: Session = Depends(get_read_session)):
    return {"findings": [_finding(f) for f in review_latency.detect(session)]}


@router.get("/backlog")
def get_backlog(session: Session = Depends(get_read_session)):
    finding, segments = backlog.detect(session)
    return {"finding": _finding(finding), "segments": [asdict(s) for s in segments]}


BY_PROJECT_SQL = """
SELECT waste_type, repo, component, n_items, hours, cost
FROM v_waste_by_project
ORDER BY cost DESC NULLS LAST, hours DESC
"""

CI_BY_PROJECT_SQL = """
SELECT repo, SUM(runner_minutes) AS minutes, count(*) AS n_runs
FROM v_ci_waste_minutes
GROUP BY repo
"""


@router.get("/by-project")
def get_by_project(session: Session = Depends(get_read_session)):
    """Every detector re-cut by (project, component) — what WasteView draws.

    CI is priced here rather than in the view: the per-minute figure needs a
    citation, and a missing citation must fail closed in Python instead of
    becoming a silently wrong number inside SQL. If the citation is absent
    the CI rows come back with a null cost and a reason, exactly like
    latency does by design.
    """
    rows = [
        {
            "type": r[0],
            "project": r[1],
            "component": r[2],
            "nItems": int(r[3] or 0),
            "hours": float(r[4] or 0),
            "amountRupees": float(r[5]) if r[5] is not None else None,
        }
        for r in session.execute(text(BY_PROJECT_SQL)).all()
    ]

    cfg = load_config()
    ci_reason: str | None = None

    for repo, minutes, n_runs in session.execute(text(CI_BY_PROJECT_SQL)).all():
        cost, reason = ci_waste.price(Decimal(str(minutes)), cfg)
        ci_reason = ci_reason or (reason.splitlines()[0] if reason else None)
        rows.append(
            {
                "type": "ci",
                "project": repo,
                "component": None,
                "nItems": int(n_runs),
                "hours": float(minutes) / 60.0,
                "amountRupees": float(cost) if cost is not None else None,
            }
        )

    return {
        "rows": rows,
        "unpriced": {
            "latency": (
                "Waiting is not paid engineer time. Review latency is reported "
                "as duration and is never converted to rupees."
            ),
            "ci": ci_reason,
        },
        "keyPerson": (
            "Computed offline only (`python -m app.waste.key_person`). It reads "
            "a per-actor view that is deliberately never granted to the API "
            "role, so it cannot be served over HTTP at any aggregation."
        ),
    }
