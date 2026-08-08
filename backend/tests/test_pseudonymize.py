"""
Pseudonymisation tests.

These back the answer to "isn't this employee surveillance?", so they are
written adversarially: each one describes a way the privacy claim could be
false, not a way the happy path works.
"""

from __future__ import annotations

import sqlite3

import pytest

from app.ingestion.pseudonymize import (
    IdentityLeak,
    IdentityStore,
    SaltRotated,
    actor_hash,
    assert_identity_db_ignored,
    assert_no_identity,
    is_bot,
)

SALT = "test-salt"


# --- hashing -------------------------------------------------------------


def test_same_login_and_salt_gives_same_hash():
    assert actor_hash("kafkadev", SALT) == actor_hash("kafkadev", SALT)


def test_hash_is_case_insensitive():
    """GitHub logins are case-insensitive; two cases must not be two actors."""
    assert actor_hash("KafkaDev", SALT) == actor_hash("kafkadev", SALT)


def test_different_salt_gives_different_hash():
    assert actor_hash("kafkadev", SALT) != actor_hash("kafkadev", "other-salt")


def test_different_login_gives_different_hash():
    assert actor_hash("alpha", SALT) != actor_hash("beta", SALT)


def test_hash_is_sixteen_hex_chars():
    digest = actor_hash("kafkadev", SALT)
    assert len(digest) == 16
    int(digest, 16)  # raises if not hex


def test_hash_does_not_contain_the_login():
    assert "kafkadev" not in actor_hash("kafkadev", SALT)


def test_empty_login_is_refused():
    with pytest.raises(ValueError):
        actor_hash("   ", SALT)


# --- bot filtering -------------------------------------------------------


@pytest.mark.parametrize(
    "login",
    [
        "dependabot",
        "dependabot[bot]",
        "renovate",
        "renovate[bot]",
        "github-actions",
        "github-actions[bot]",
        "asfgit",
        "apache-kafka-bot",
        "apache-flink-bot",
        "ASFGIT",
    ],
)
def test_known_bots_are_filtered(login):
    assert is_bot(login)


@pytest.mark.parametrize("login", ["kafkadev", "junrao", "abbot", "robotnik"])
def test_humans_are_not_filtered(login):
    """`abbot` and `robotnik` contain 'bot' and must survive a naive filter."""
    assert not is_bot(login)


def test_graphql_typename_bot_wins_over_the_name_list():
    assert is_bot("some-unknown-account", typename="Bot")
    assert not is_bot("some-unknown-account", typename="User")


def test_missing_login_is_treated_as_not_a_person():
    assert is_bot(None)
    assert is_bot("")


def test_bots_never_enter_the_identity_store(tmp_identity_db):
    store = IdentityStore(path=tmp_identity_db, salt=SALT)
    assert store.record("dependabot[bot]") is None
    assert store.record("kafkadev") is not None
    assert store.count() == 1
    store.close()


# --- the identity store --------------------------------------------------


def test_store_is_idempotent(tmp_identity_db):
    store = IdentityStore(path=tmp_identity_db, salt=SALT)
    first = store.record("kafkadev")
    second = store.record("KafkaDev")
    assert first == second
    assert store.count() == 1
    store.close()


def test_store_round_trips_forward_only(tmp_identity_db):
    store = IdentityStore(path=tmp_identity_db, salt=SALT)
    digest = store.record("kafkadev")
    assert store.hash_for("kafkadev") == digest
    store.close()


def test_store_offers_no_reverse_lookup():
    """A hash -> login helper is a re-identification tool. It must not exist."""
    forbidden = {"login_for", "reverse", "unhash", "identify", "logins"}
    assert not forbidden & set(dir(IdentityStore))


def test_rotating_the_salt_is_refused(tmp_identity_db):
    IdentityStore(path=tmp_identity_db, salt=SALT).close()
    with pytest.raises(SaltRotated):
        IdentityStore(path=tmp_identity_db, salt="a-different-salt")


def test_identity_store_is_sqlite_not_postgres(tmp_identity_db):
    """The physical separation IS the privacy claim — assert it directly."""
    store = IdentityStore(path=tmp_identity_db, salt=SALT)
    store.record("kafkadev")
    store.close()
    assert tmp_identity_db.exists()
    with sqlite3.connect(tmp_identity_db) as conn:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert "identity_map" in tables


# --- the guard every writer calls ----------------------------------------


@pytest.mark.parametrize(
    "column",
    [
        "login",
        "author_login",
        "email",
        "author_email",
        "name",
        "display_name",
        "salary",
        "annual_salary",
        "LOGIN",
    ],
)
def test_identity_columns_are_refused(column):
    with pytest.raises(IdentityLeak):
        assert_no_identity({column: ["anything"]})


def test_clean_frame_passes():
    assert_no_identity(
        {"actor_hash": ["ab12"], "activity": ["commit"], "ts": ["2026-01-01"]}
    )


def test_allow_exempts_a_genuine_false_positive():
    assert_no_identity({"component_name": ["core"]}, allow=["component_name"])


def test_accepts_a_pandas_dataframe():
    pd = pytest.importorskip("pandas")
    with pytest.raises(IdentityLeak):
        assert_no_identity(pd.DataFrame({"actor_hash": ["x"], "email": ["a@b.c"]}))
    assert_no_identity(pd.DataFrame({"actor_hash": ["x"], "activity": ["commit"]}))


def test_accepts_a_list_of_dicts():
    with pytest.raises(IdentityLeak):
        assert_no_identity([{"actor_hash": "x"}, {"login": "kafkadev"}])


def test_accepts_a_bare_column_list():
    with pytest.raises(IdentityLeak):
        assert_no_identity(["actor_hash", "email"])


def test_strict_mode_catches_identity_in_a_cleanly_named_column():
    """The column is called `attrs`; the value is still an email address."""
    payload = [{"actor_hash": "ab12", "attrs": "someone@apache.org"}]
    assert_no_identity(payload)  # name check alone lets it through
    with pytest.raises(IdentityLeak):
        assert_no_identity(payload, strict=True)


# --- repo hygiene --------------------------------------------------------


def test_identity_db_is_gitignored_and_untracked():
    assert_identity_db_ignored()
