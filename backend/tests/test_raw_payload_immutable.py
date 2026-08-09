"""
raw_payload is append-only, and these prove it against real Postgres.

The point of "land raw, then map" is that a mapping bug costs a re-run rather
than a re-fetch. That only holds while the raw rows survive, and a convention
did not hold: two test fixtures deleted 5,632 PR payloads and 79,085 CI rows
between them, twice.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db.purge import PURGE_FLAG, PurgeRefused, purge_raw_payload
from app.db.session import get_write_engine, write_session

#: A row that belongs to no connector, so nothing real is ever at risk here.
FIXTURE = {
    "source": "esi-test-immutability",
    "entity_type": "probe",
    "entity_id": "probe-1",
}


@pytest.fixture
def probe_row(pg_engine):
    """Insert one disposable row, and take it away again through the hatch."""
    with write_session() as session:
        session.execute(
            text(
                "INSERT INTO raw_payload (source, entity_type, entity_id, body) "
                "VALUES (:source, :entity_type, :entity_id, '{\"n\": 1}'::jsonb) "
                "ON CONFLICT (source, entity_type, entity_id) DO NOTHING"
            ),
            FIXTURE,
        )
        session.commit()
    yield FIXTURE
    with write_session() as session:
        purge_raw_payload(
            session, source=FIXTURE["source"], entity_ids=[FIXTURE["entity_id"]]
        )
        session.commit()


def _count(conn, source: str) -> int:
    return conn.execute(
        text("SELECT count(*) FROM raw_payload WHERE source = :s"), {"s": source}
    ).scalar()


# --- the restriction ------------------------------------------------------


def test_the_write_role_cannot_delete_from_raw_payload(probe_row):
    """The headline claim, run as the role ingestion actually uses."""
    with pytest.raises(DBAPIError) as excinfo, get_write_engine().begin() as conn:
        conn.execute(
            text("DELETE FROM raw_payload WHERE source = :s"),
            {"s": probe_row["source"]},
        )
    assert "append-only" in str(excinfo.value)


def test_the_write_role_cannot_truncate_raw_payload():
    """TRUNCATE does not fire row-level DELETE triggers, so a DELETE trigger
    on its own leaves this wide open. It needs its own statement trigger."""
    with pytest.raises(DBAPIError) as excinfo, get_write_engine().begin() as conn:
        conn.execute(text("TRUNCATE raw_payload"))
    assert "append-only" in str(excinfo.value)


def test_a_refused_delete_leaves_the_row_in_place(probe_row):
    with get_write_engine().begin() as conn, pytest.raises(DBAPIError):
        conn.execute(
            text("DELETE FROM raw_payload WHERE source = :s"),
            {"s": probe_row["source"]},
        )
    with get_write_engine().connect() as conn:
        assert _count(conn, probe_row["source"]) == 1


def test_delete_and_truncate_are_not_granted_to_the_write_role(pg_engine):
    """Belt and braces beside the trigger. This grant is what enforces once
    `esi` stops being a superuser; today the trigger is what does."""
    with pg_engine.connect() as conn:
        granted = {
            r[0]
            for r in conn.execute(
                text(
                    "SELECT privilege_type FROM information_schema.table_privileges "
                    "WHERE table_name = 'raw_payload' AND grantee = 'esi'"
                )
            )
        }
    assert "DELETE" not in granted
    assert "TRUNCATE" not in granted


def test_the_triggers_are_enable_always(pg_engine):
    """ENABLE ORIGIN, the default, is skipped in replica role — one
    `SET session_replication_role = 'replica'` from disarming both."""
    with pg_engine.connect() as conn:
        modes = dict(
            conn.execute(
                text(
                    "SELECT tgname, tgenabled FROM pg_trigger "
                    "WHERE tgrelid = 'raw_payload'::regclass AND NOT tgisinternal"
                )
            ).all()
        )
    assert modes.get("raw_payload_no_delete") == "A"
    assert modes.get("raw_payload_no_truncate") == "A"


# --- ingestion still works ------------------------------------------------


def test_ingestion_can_still_insert(pg_engine):
    with write_session() as session:
        session.execute(
            text(
                "INSERT INTO raw_payload (source, entity_type, entity_id, body) "
                "VALUES (:source, :entity_type, 'insert-1', '{\"n\": 1}'::jsonb) "
                "ON CONFLICT (source, entity_type, entity_id) DO NOTHING"
            ),
            {"source": FIXTURE["source"], "entity_type": FIXTURE["entity_type"]},
        )
        session.commit()
    with pg_engine.connect() as conn:
        assert _count(conn, FIXTURE["source"]) >= 1
    with write_session() as session:
        purge_raw_payload(
            session, source=FIXTURE["source"], entity_ids=["insert-1"]
        )
        session.commit()


def test_the_existing_upsert_still_updates_in_place(probe_row):
    """ON CONFLICT DO UPDATE needs INSERT *and* UPDATE. Both are kept; only
    DELETE and TRUNCATE went. Every connector re-runs through this path."""
    from datetime import UTC, datetime

    from sqlalchemy.dialects.postgresql import insert

    from app.db.models import RawPayload

    with write_session() as session:
        stmt = insert(RawPayload).values(
            [{**probe_row, "body": {"n": 2, "refetched": True}}]
        )
        session.execute(
            stmt.on_conflict_do_update(
                index_elements=["source", "entity_type", "entity_id"],
                set_={"body": stmt.excluded.body, "fetched_at": datetime.now(UTC)},
            )
        )
        session.commit()

    with get_write_engine().connect() as conn:
        body = conn.execute(
            text(
                "SELECT body FROM raw_payload WHERE source = :s AND entity_id = :e"
            ),
            {"s": probe_row["source"], "e": probe_row["entity_id"]},
        ).scalar()
        assert body == {"n": 2, "refetched": True}
        assert _count(conn, probe_row["source"]) == 1, "upsert must not duplicate"


def test_a_real_connector_upsert_path_is_unaffected(pg_engine):
    """The actual function ingestion calls, not a hand-written INSERT."""
    from app.ingestion.github_connector import _write_page

    node = {
        "number": 999_001,
        "title": "KAFKA-1: probe",
        "body": "",
        "headRefName": "probe",
        "author": {"login": "octocat", "__typename": "User"},
        "reviews": {"nodes": []},
        "timelineItems": {"nodes": []},
    }
    with write_session() as session:
        assert _write_page(session, "esi-test/immutability", [node]) == 1
        assert _write_page(session, "esi-test/immutability", [node]) == 1
        session.commit()
    with pg_engine.connect() as conn:
        assert conn.execute(
            text(
                "SELECT count(*) FROM raw_payload WHERE entity_id = "
                "'esi-test/immutability#999001'"
            )
        ).scalar() == 1
    with write_session() as session:
        purge_raw_payload(
            session,
            source="github_graphql",
            entity_ids=["esi-test/immutability#999001"],
        )
        session.commit()


def test_real_ingested_rows_are_untouched_by_all_of_the_above(pg_engine):
    """The four real sources must still be there when this file finishes."""
    with pg_engine.connect() as conn:
        sources = dict(
            conn.execute(
                text("SELECT source, count(*) FROM raw_payload GROUP BY 1")
            ).all()
        )
    for source in ("git_local", "github_graphql", "github_actions", "asf_jira"):
        assert sources.get(source, 0) > 0, f"{source} payloads have gone missing"


# --- the escape hatch -----------------------------------------------------


def test_the_purge_path_works_but_only_for_named_rows(probe_row):
    with write_session() as session:
        removed = purge_raw_payload(
            session, source=probe_row["source"], entity_ids=[probe_row["entity_id"]]
        )
        session.commit()
    assert removed == 1
    with get_write_engine().connect() as conn:
        assert _count(conn, probe_row["source"]) == 0


def test_purging_a_whole_source_needs_saying_so_out_loud():
    """A purge with no id list is a purge of everything from that connector —
    the exact shape of the accident this mechanism exists to prevent."""
    with write_session() as session, pytest.raises(PurgeRefused, match="entity_ids"):
        purge_raw_payload(session, source="github_graphql")


def test_the_hatch_closes_when_the_transaction_ends(probe_row):
    """SET LOCAL, not SET. A purge must not leave deletes enabled for whatever
    the connection does next."""
    with write_session() as session:
        purge_raw_payload(session, source=probe_row["source"], entity_ids=["nope"])
        session.commit()
        assert session.execute(
            text(f"SELECT current_setting('{PURGE_FLAG}', true)")
        ).scalar() in (None, "", "off")


def test_nothing_outside_the_purge_module_opens_the_hatch():
    """If app code could set this flag, the trigger would be decorative."""
    from pathlib import Path

    app = Path(__file__).resolve().parents[1] / "app"
    offenders = [
        str(path.relative_to(app))
        for path in app.rglob("*.py")
        if PURGE_FLAG in path.read_text(encoding="utf-8") and path.name != "purge.py"
    ]
    assert not offenders, f"{PURGE_FLAG} set outside purge.py: {offenders}"


def test_no_app_module_deletes_from_raw_payload():
    """The guard the earlier incident deserved: tests were covered, app code
    was not."""
    import re
    from pathlib import Path

    app = Path(__file__).resolve().parents[1] / "app"
    pattern = re.compile(r"DELETE\s+FROM\s+raw_payload", re.IGNORECASE)
    offenders = [
        str(path.relative_to(app))
        for path in app.rglob("*.py")
        if pattern.search(path.read_text(encoding="utf-8")) and path.name != "purge.py"
    ]
    assert not offenders, f"raw_payload deleted outside purge.py: {offenders}"
