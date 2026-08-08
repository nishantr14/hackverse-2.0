"""
Actor pseudonymisation — privacy by design, from the first commit ingested.
Owner: Nishant (ingestion lane).
Phase: Tier 0. Wired in before any real GitHub/Jira row is written.

    actor_hash = sha256(login + PSEUDONYMIZATION_SALT).hexdigest()[:16]

The login -> hash mapping is written to data/identity.db, a SQLite file that
lives PHYSICALLY OUTSIDE Postgres and is opened only by this package. That
physical separation is the entire privacy claim: a person with a full dump of
the analytics database still cannot name anyone, because the join key does not
exist there.

Three deliberate omissions, each of which would be easy to add and would
quietly destroy the claim:

* No hash -> login reverse lookup. Ingestion never needs one. A convenience
  function here is a re-identification tool for anyone who gets the file.
* No login column on any object this module returns.
* No bot ever becomes an actor. Apache repos are heavily automated; unfiltered,
  `github-actions` and `asfgit` outrank every human on commit count and every
  cost number downstream is wrong.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import subprocess
import sys
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import REPO_ROOT, get_settings

# ---------------------------------------------------------------------
# Bot filtering
# ---------------------------------------------------------------------

#: Exact logins (after normalisation) that are never actors.
BOT_LOGINS: frozenset[str] = frozenset(
    {
        "dependabot",
        "dependabot-preview",
        "renovate",
        "renovate-bot",
        "github-actions",
        "asfgit",
        "asf-ci",
        "codecov",
    }
)

#: Apache runs a family of per-project bots: apache-kafka-bot, apache-flink-bot.
BOT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^apache-.*-bot$"),
    re.compile(r"^.*\[bot\]$"),
)

# ---------------------------------------------------------------------
# Identity leak detection
# ---------------------------------------------------------------------

#: Any column whose name contains one of these is identity and must never
#: reach Postgres. Substring matching, deliberately over-eager: a false
#: positive costs one `allow` argument, a false negative costs the product.
IDENTITY_TOKENS: tuple[str, ...] = ("login", "email", "name", "salary")


class IdentityLeak(AssertionError):
    """Raised when identity is about to be written to the analytics database."""


class SaltRotated(RuntimeError):
    """Raised when PSEUDONYMIZATION_SALT no longer matches the identity store."""


# ---------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------


def actor_hash(login: str, salt: str | None = None) -> str:
    """Pseudonymous, stable, non-reversible-in-practice id for a contributor.

    Deterministic: the same login and salt always give the same hash, so a
    re-ingest does not fragment one person into several actors.

    Logins are lowercased first. GitHub logins are case-insensitive, so
    `KafkaDev` and `kafkadev` are one person and must not become two actors.
    """
    if not login or not login.strip():
        raise ValueError("cannot hash an empty login")
    salt = get_settings().pseudonymization_salt if salt is None else salt
    digest = hashlib.sha256((login.strip().lower() + salt).encode("utf-8"))
    return digest.hexdigest()[:16]


def _salt_fingerprint(salt: str) -> str:
    """Identifies WHICH salt built a store, without storing the salt itself."""
    return hashlib.sha256(("fingerprint:" + salt).encode("utf-8")).hexdigest()[:16]


def normalise_login(login: str) -> str:
    return login.strip().lower()


def is_bot(login: str | None, typename: str | None = None) -> bool:
    """True if this account is automation.

    `typename` is GitHub GraphQL's `__typename` on an author — 'Bot' or 'User'.
    Trust it when present; fall back to the name list when it is absent, which
    it always is for git-log authors and Jira users.
    """
    if typename is not None and typename.strip().lower() == "bot":
        return True
    if not login:
        return True  # an unattributable event is not a person either
    handle = normalise_login(login)
    if handle in BOT_LOGINS:
        return True
    if any(pattern.match(handle) for pattern in BOT_PATTERNS):
        return True
    # `dependabot[bot]` normalises through the pattern above; this catches the
    # bare-suffix form some payloads use.
    return handle.removesuffix("[bot]") in BOT_LOGINS


# ---------------------------------------------------------------------
# The identity store
# ---------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS identity_map (
    login         TEXT PRIMARY KEY,
    actor_hash    TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    source        TEXT
);
CREATE INDEX IF NOT EXISTS idx_identity_hash ON identity_map (actor_hash);

CREATE TABLE IF NOT EXISTS store_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class IdentityStore:
    """The only place a login and its hash sit side by side.

    Opened only from `app.ingestion`. `/privacy-audit` greps for constructions
    of this class outside that package and treats any hit as a finding.
    """

    def __init__(self, path: Path | None = None, salt: str | None = None) -> None:
        settings = get_settings()
        self.path = Path(path) if path is not None else settings.identity_db_file
        self.salt = settings.pseudonymization_salt if salt is None else salt
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.executescript(_SCHEMA)
        self._check_salt()
        self._conn.commit()

    def _check_salt(self) -> None:
        """Refuse to append to a store built with a different salt.

        A rotated salt silently produces a second hash for every existing
        person, doubling the actor count and halving every per-actor figure.
        Failing here is much cheaper than explaining that number on stage.
        """
        fingerprint = _salt_fingerprint(self.salt)
        row = self._conn.execute(
            "SELECT value FROM store_meta WHERE key = 'salt_fingerprint'"
        ).fetchone()
        if row is None:
            self._conn.execute(
                "INSERT INTO store_meta (key, value) VALUES ('salt_fingerprint', ?)",
                (fingerprint,),
            )
        elif row[0] != fingerprint:
            raise SaltRotated(
                f"{self.path} was built with a different PSEUDONYMIZATION_SALT. "
                "Every existing actor_hash would change. Restore the old salt, "
                "or delete the identity store AND re-ingest from scratch."
            )

    def record(self, login: str, source: str = "github") -> str | None:
        """Map a login to its hash and return the hash. Returns None for bots.

        Idempotent: recording the same login twice is a no-op and returns the
        same hash.
        """
        if is_bot(login):
            return None
        handle = normalise_login(login)
        digest = actor_hash(handle, self.salt)
        self._conn.execute(
            "INSERT INTO identity_map (login, actor_hash, first_seen_at, source) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(login) DO NOTHING",
            (handle, digest, datetime.now(UTC).isoformat(), source),
        )
        return digest

    def record_many(
        self, logins: Iterable[str], source: str = "github"
    ) -> dict[str, str]:
        """Bulk form of `record`. Bots are dropped, not raised on."""
        out: dict[str, str] = {}
        for login in logins:
            digest = self.record(login, source=source)
            if digest is not None:
                out[normalise_login(login)] = digest
        return out

    def hash_for(self, login: str) -> str | None:
        """Look up an already-recorded login. Forward direction only."""
        row = self._conn.execute(
            "SELECT actor_hash FROM identity_map WHERE login = ?",
            (normalise_login(login),),
        ).fetchone()
        return None if row is None else str(row[0])

    def count(self) -> int:
        return int(
            self._conn.execute("SELECT count(*) FROM identity_map").fetchone()[0]
        )

    def commit(self) -> None:
        self._conn.commit()

    def close(self) -> None:
        self._conn.commit()
        self._conn.close()


@contextmanager
def identity_store(
    path: Path | None = None, salt: str | None = None
) -> Iterator[IdentityStore]:
    """Open the identity store for the duration of one ingestion run."""
    store = IdentityStore(path=path, salt=salt)
    try:
        yield store
        store.commit()
    finally:
        store.close()


# ---------------------------------------------------------------------
# The guard every writer calls
# ---------------------------------------------------------------------


def assert_no_identity(
    frame: Any, *, allow: Iterable[str] = (), strict: bool = False
) -> None:
    """Raise `IdentityLeak` if `frame` carries identity into the analytics DB.

    Accepts a pandas DataFrame, a dict of column -> values, a list of dicts, or
    a bare sequence of column names — whatever the caller happens to be holding
    at the moment before a write.

    `allow` exempts a specific column name for a genuine false positive (a
    column called `component_name`, say). Name the column, never widen the
    token list.

    `strict=True` additionally scans string values for an `@`, catching identity
    smuggled in a correctly-named column. Off by default because it is O(cells)
    and ingestion runs on a clock; the writers turn it on for their first run.
    """
    exempt = {c.lower() for c in allow}
    columns = _columns_of(frame)

    offenders = sorted(
        column
        for column in columns
        if column.lower() not in exempt
        and any(token in column.lower() for token in IDENTITY_TOKENS)
    )
    if offenders:
        raise IdentityLeak(
            f"identity columns {offenders} would reach the analytics database. "
            "Pseudonymise with actor_hash() and drop the source column. "
            "Postgres holds no login, email, name or salary — see docs/schema.sql."
        )

    if strict:
        for column, value in _email_like_cells(frame):
            raise IdentityLeak(
                f"column {column!r} contains an email-shaped value {value!r}; "
                "the column name is clean but the data is not"
            )


def _columns_of(frame: Any) -> list[str]:
    cols = getattr(frame, "columns", None)
    if cols is not None:
        return [str(c) for c in cols]
    if isinstance(frame, dict):
        return [str(c) for c in frame]
    if isinstance(frame, (list, tuple)) and frame and isinstance(frame[0], dict):
        seen: dict[str, None] = {}
        for row in frame:
            for key in row:
                seen[str(key)] = None
        return list(seen)
    if isinstance(frame, (list, tuple, set, frozenset)):
        return [str(c) for c in frame]
    raise TypeError(f"cannot read column names from {type(frame).__name__}")


def _email_like_cells(frame: Any) -> Iterator[tuple[str, str]]:
    rows: Iterable[dict[str, Any]]
    if hasattr(frame, "to_dict"):
        rows = frame.to_dict(orient="records")
    elif isinstance(frame, dict):
        rows = [frame]
    elif isinstance(frame, (list, tuple)) and frame and isinstance(frame[0], dict):
        rows = frame
    else:
        return
    for row in rows:
        for column, value in row.items():
            if isinstance(value, str) and "@" in value and " " not in value.strip():
                yield str(column), value


# ---------------------------------------------------------------------
# Repo hygiene
# ---------------------------------------------------------------------


def assert_identity_db_ignored(repo_root: Path | None = None) -> None:
    """Raise if data/identity.db could ever be committed.

    Checked in tests and by `/privacy-audit`. A gitignored file is one `git add
    -f` away from the repo, so this also asserts it is not currently tracked.
    """
    root = repo_root or REPO_ROOT
    relative = get_settings().identity_db_file.relative_to(root).as_posix()

    gitignore = root / ".gitignore"
    if not gitignore.exists():
        raise IdentityLeak(".gitignore is missing; the identity store is unprotected")
    if relative not in gitignore.read_text(encoding="utf-8"):
        raise IdentityLeak(
            f"{relative} is not named in .gitignore. A glob may cover it, but the "
            "rule must be explicit and greppable."
        )

    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", relative],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if tracked.returncode == 0:
        raise IdentityLeak(
            f"{relative} IS TRACKED BY GIT. Remove it from the index immediately: "
            f"git rm --cached {relative}"
        )


#: The salt as shipped. If two people both leave it at this, their hashes
#: match — which looks like agreement and is actually the absence of a salt.
DEFAULT_SALT = "change-me"


def salt_fingerprint(salt: str | None = None) -> str:
    """A shareable check that two machines agree on PSEUDONYMIZATION_SALT.

    Everyone runs ingestion locally, so a salt that differs between two
    laptops produces two actor_hash values for one person. Nothing errors —
    the actor count simply inflates, the k-anonymity floor weakens, and the
    two halves of somebody's work never join. It is the kind of divergence
    that is invisible until the numbers are on a slide.

    Compare THIS, never the salt. It is a hash under a different prefix than
    actor_hash uses, so posting it in a group chat does not hand anyone a way
    to reverse the actor hashes.
    """
    salt = get_settings().pseudonymization_salt if salt is None else salt
    return _salt_fingerprint(salt)


def warn_if_default_salt() -> bool:
    """Shout at the start of every ingestion run if the salt was never set.

    Called by each connector's main. Nothing fails — refusing to ingest at
    hour 4 of a 36-hour build would be worse than the problem — but it must be
    impossible to fetch a hundred thousand rows without seeing this.

    A salt change is not a re-map, it is a RE-FETCH. GitHub logins and Jira
    usernames are hashed inside the scrubber and never written to
    identity.db, so there is nothing on disk to re-hash them from. Decide the
    salt before the data, not after.
    """
    if get_settings().pseudonymization_salt != DEFAULT_SALT:
        return False
    print(
        "\n  !! PSEUDONYMIZATION_SALT is still 'change-me'.\n"
        f"  !! fingerprint {salt_fingerprint()} — everyone matching means "
        "nobody has a salt.\n"
        "  !! Agree one value across all four .env files BEFORE ingesting: "
        "changing it later means re-fetching, not re-mapping.\n",
        file=sys.stderr,
    )
    return True


def main(argv: Sequence[str] | None = None) -> int:
    """`python -m app.ingestion.pseudonymize` — print the salt fingerprint.

    Deliberately a CLI and not a field on /meta. /meta is served to the
    browser; this is for four people to compare in a chat window.
    """
    settings = get_settings()
    print(f"salt fingerprint  {salt_fingerprint()}")
    print("compare this with your teammates. Same fingerprint = same hashes.")
    if settings.pseudonymization_salt == DEFAULT_SALT:
        print(
            "\nWARNING: PSEUDONYMIZATION_SALT is still the shipped default.\n"
            "  Everyone matches because nobody has a salt, not because we agree.\n"
            "  Set one identical value in every .env before ingesting."
        )
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
