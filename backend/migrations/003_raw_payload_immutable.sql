-- =====================================================================
-- 003 — raw_payload becomes append-only at the database level.
--
-- raw_payload is the evidence layer. "Land raw, then map" only buys anything
-- if the raw rows survive: a mapping bug is supposed to cost a 20-second
-- re-run, not a 40-minute re-fetch. Until now that was a convention, and a
-- convention lost 5,632 PR payloads and 79,085 CI rows to a test fixture.
--
-- WHY THIS IS A TRIGGER AND NOT JUST A REVOKE
--
-- The obvious fix is `REVOKE DELETE ON raw_payload FROM esi`. Measured
-- against this database, that does nothing:
--
--     CREATE TEMP TABLE probe(x int);  INSERT INTO probe VALUES (1);
--     REVOKE DELETE ON probe FROM esi;
--     DELETE FROM probe;               -- 1 row deleted
--
-- `esi` is a SUPERUSER and owns both the database and the table. Superusers
-- bypass every privilege check, and an owner can re-grant to itself anyway.
-- The REVOKE below is still issued — it is correct, it costs nothing, and it
-- becomes the real guarantee the day `esi` stops being a superuser (see the
-- note at the bottom) — but it is NOT what stops a delete today. The trigger
-- is, because triggers fire for superusers too.
--
-- TRUNCATE IS A SEPARATE HOLE. It does not fire row-level DELETE triggers, so
-- a DELETE trigger alone leaves `TRUNCATE raw_payload` wide open. Hence the
-- second, statement-level trigger.
--
-- Idempotent: safe to re-run.
-- =====================================================================

CREATE OR REPLACE FUNCTION raw_payload_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- The deliberate escape hatch. A purge must be an explicit, per
    -- transaction act that says so in the SQL, not a DELETE that happens to
    -- be reachable. See app/db/purge.py — nothing else in the codebase sets
    -- this, and a test asserts that.
    IF current_setting('esi.allow_raw_purge', true) = 'on' THEN
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RETURN NULL;
    END IF;

    RAISE EXCEPTION
        'raw_payload is append-only: % refused', TG_OP
        USING HINT =
            'raw_payload is the evidence layer — mapping bugs are re-run, not '
            're-fetched. If you genuinely mean to purge, use '
            'app.db.purge.purge_raw_payload(), which sets '
            'esi.allow_raw_purge for one transaction and says so out loud.',
        ERRCODE = 'insufficient_privilege';
END;
$$;

DROP TRIGGER IF EXISTS raw_payload_no_delete ON raw_payload;
CREATE TRIGGER raw_payload_no_delete
    BEFORE DELETE ON raw_payload
    FOR EACH ROW EXECUTE FUNCTION raw_payload_append_only();

DROP TRIGGER IF EXISTS raw_payload_no_truncate ON raw_payload;
CREATE TRIGGER raw_payload_no_truncate
    BEFORE TRUNCATE ON raw_payload
    FOR EACH STATEMENT EXECUTE FUNCTION raw_payload_append_only();

-- ENABLE ALWAYS, not the default ENABLE ORIGIN: the default form is skipped
-- when the session is in replica role, which is one `SET session_replication_role`
-- away from silently disarming both of these.
ALTER TABLE raw_payload ENABLE ALWAYS TRIGGER raw_payload_no_delete;
ALTER TABLE raw_payload ENABLE ALWAYS TRIGGER raw_payload_no_truncate;

-- Correct today, load-bearing tomorrow. INSERT, SELECT and UPDATE all stay:
-- ingestion upserts with ON CONFLICT DO UPDATE, which needs INSERT *and*
-- UPDATE, and removing either would break every connector.
REVOKE DELETE, TRUNCATE ON raw_payload FROM esi;

-- The remaining step is not ours to take unilaterally, so it is written down
-- rather than done: `esi` is a superuser, which makes every grant on every
-- table advisory. The one-line fix, once someone confirms nothing in infra
-- depends on it, is
--
--     ALTER ROLE esi NOSUPERUSER;
--
-- after which the REVOKE above enforces on its own and the triggers become
-- defence in depth rather than the only defence.
