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

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

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
