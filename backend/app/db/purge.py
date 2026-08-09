"""
The one deliberate way to remove rows from the evidence layer.
Owner: Nishant (ingestion lane).

`raw_payload` is append-only, enforced by triggers (migrations/003). This
module is the single escape hatch, and it is written to be conspicuous rather
than convenient: it names the rows, it says how many it removed, and it opens
the hatch for exactly one transaction.

    from app.db.purge import purge_raw_payload
    purge_raw_payload(session, source="github_graphql", entity_ids=[...])

WHY NOT JUST GRANT DELETE FOR TESTS
    Because that is how the rows were lost the first time. Two test fixtures
    tidied up after themselves with `DELETE FROM raw_payload WHERE source=...`
    against the real database, and every suite run took 5,632 fetched PR
    payloads with it. The cost of that convenience was two re-fetches.

    Test cleanup now names its own rows and comes through here, so a purge is
    something someone chose, not something a fixture did on the way past.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

#: Read by the trigger in migrations/003. Set with SET LOCAL so it reverts at
#: the end of the transaction whether that transaction commits or rolls back.
PURGE_FLAG = "esi.allow_raw_purge"


class PurgeRefused(RuntimeError):
    """The caller asked for something too broad to be an accident."""


def purge_raw_payload(
    session: Session,
    *,
    source: str,
    entity_ids: Sequence[str] | None = None,
    entity_type: str | None = None,
    i_understand_this_is_evidence: bool = False,
) -> int:
    """Delete named raw_payload rows. Returns the number removed.

    `entity_ids` is required unless the caller passes the long keyword. A
    purge with no id list is a purge of an entire source — which is exactly
    the shape of the accident this whole mechanism exists to prevent, so it
    has to be spelled out.
    """
    if not source:
        raise PurgeRefused("a purge must name a source")
    if not entity_ids and not i_understand_this_is_evidence:
        raise PurgeRefused(
            f"refusing to purge every {source!r} row. Pass entity_ids to name "
            "the rows, or i_understand_this_is_evidence=True if you really do "
            "mean all of them — re-fetching them costs an API round trip and, "
            "for the ASF, someone else's bandwidth."
        )

    clauses = ["source = :source"]
    params: dict[str, object] = {"source": source}
    if entity_ids is not None:
        clauses.append("entity_id = ANY(:entity_ids)")
        params["entity_ids"] = list(entity_ids)
    if entity_type is not None:
        clauses.append("entity_type = :entity_type")
        params["entity_type"] = entity_type

    # SET LOCAL: the hatch closes when this transaction ends, either way.
    session.execute(text(f"SET LOCAL {PURGE_FLAG} = 'on'"))
    removed = session.execute(
        text(f"DELETE FROM raw_payload WHERE {' AND '.join(clauses)}"), params
    ).rowcount
    logger.warning(
        "PURGED %d raw_payload rows (source=%s, %s)",
        removed,
        source,
        f"{len(entity_ids)} named ids" if entity_ids is not None else "ALL",
    )
    return removed or 0
