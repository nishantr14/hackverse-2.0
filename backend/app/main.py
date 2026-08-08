"""
Engineering Spend Intelligence — FastAPI entrypoint.
Owner: shared infra (whoever adds a router, wire it here).
Phase: Tier 0.

Mounts the routers in app/api/ (spend, waste, process, simulate). Run with
`uvicorn app.main:app --reload` (see README for the full command and the
Docker alternative).
"""

from fastapi import FastAPI

app = FastAPI(title="Engineering Spend Intelligence")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


# TODO: include_router() for spend/waste/process/simulate as they land,
# CORS config for the Vite dev server origin.
