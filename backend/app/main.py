"""
Engineering Spend Intelligence — FastAPI entrypoint.
Owner: Nishant (infra lane).
Phase: Tier 0.

Run with `uvicorn app.main:app --reload` from backend/ (see README).

Router wiring is deliberately tolerant: `backend/app/api/` belongs to the lane
owner behind each router, and those modules are still stubs. Each one is
mounted the moment it defines `router`, and skipped until then. That means
Diljit and Dipen add one line to their own file and their routes appear — no
edit to this file, no merge conflict on it, and `/health` keeps working in the
meantime so the frontend always has something to boot against.
"""

from __future__ import annotations

import importlib
import logging
from types import ModuleType

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings

logger = logging.getLogger(__name__)

#: Mount order is display order in /docs. Owners: spend=Diljit, waste=Diljit,
#: process=Diljit, simulate=Dipen.
ROUTER_MODULES: tuple[str, ...] = (
    "spend",
    "waste",
    "process",
    "simulate",
    "workforce",
)

app = FastAPI(title="Engineering Spend Intelligence")

# The Vite dev server(s). Origins come from ALLOWED_ORIGINS (see config.py) so
# a deployment reachable on a LAN/VPN IP just adds itself to the list instead
# of needing a code change. Credentials stay off — this API carries no session
# and no cookie, and it must not start doing so.
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().allowed_origins_list,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/meta")
def meta() -> dict:
    """Provenance for the UI header: which dataset, which knobs, which floor.

    Every one of these values is config, not a computed number, so it is safe
    to serve without a query behind it. Assumptions are listed separately from
    settings so the UI can badge them rather than presenting them as observed.
    """
    settings = get_settings()
    return {
        "data_source": settings.data_source,
        "repos": settings.github_repo_list,
        "jira_projects": settings.jira_project_list,
        "k_anonymity_floor": settings.k_anonymity_floor,
        "k_anonymity_fallback": settings.k_anonymity_fallback,
        "assumptions": settings.assumption_summary(),
    }


def _mount_routers() -> list[str]:
    mounted: list[str] = []
    for name in ROUTER_MODULES:
        try:
            module: ModuleType = importlib.import_module(f"app.api.{name}")
        except ImportError:
            logger.warning("router module app.api.%s not importable yet", name)
            continue
        router = getattr(module, "router", None)
        if isinstance(router, APIRouter):
            app.include_router(router)
            mounted.append(name)
        else:
            logger.info("app.api.%s defines no `router` yet — skipping", name)
    return mounted


MOUNTED_ROUTERS = _mount_routers()
