"""
Apply the SQL migrations, in order.
Owner: Nishant (infra lane).

    python -m app.db.migrate            # apply everything
    python -m app.db.migrate --dry-run  # list what would run

WHY THIS EXISTS
    Docker loads `docs/schema.sql` once, at initdb, and nothing else. 002 was
    applied by `app.normalise.event_log` as a side effect of building the
    event log, and 003 onwards were applied by hand.

    The result was silent and total: every /process and /waste route returned
    a 500 reading views that had never been created, on a database that
    looked completely healthy. A fresh laptop cloning this repo would have
    reproduced it exactly, at whatever hour someone tried to set up for the
    demo.

EVERY MIGRATION IS IDEMPOTENT AND THEY ALL RUN EVERY TIME
    There is no applied-migrations ledger on purpose. Each file is written
    with CREATE OR REPLACE / DROP ... IF EXISTS, so re-running is a no-op,
    and a ledger is one more thing that can disagree with reality. If a
    migration is ever written that cannot be re-run safely, this stops being
    true and it needs the ledger — say so in the file.

001 IS SKIPPED
    It is a psql `\\ir` include of the frozen docs/schema.sql, which is a psql
    meta-command the driver cannot execute. Docker's initdb already applies
    that file. Any migration whose body needs psql is skipped with a note
    rather than a crash.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from pathlib import Path

from sqlalchemy import text

from app.db.session import get_write_engine

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"

#: A line starting with a backslash is a psql meta-command, not SQL.
PSQL_META = re.compile(r"^\s*\\", re.MULTILINE)


def migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def needs_psql(sql: str) -> bool:
    return bool(PSQL_META.search(sql))


def apply_all(*, dry_run: bool = False) -> int:
    """Run every migration in filename order. Returns the number applied."""
    files = migration_files()
    if not files:
        print(f"  no migrations found in {MIGRATIONS_DIR}", file=sys.stderr)
        return 0

    applied = 0
    engine = get_write_engine()
    for path in files:
        sql = path.read_text(encoding="utf-8")

        if needs_psql(sql):
            print(f"  SKIP   {path.name}  (psql meta-command; applied by initdb)")
            continue
        if dry_run:
            print(f"  WOULD  {path.name}")
            continue

        try:
            with engine.begin() as conn:
                conn.execute(text(sql))
        except Exception as exc:
            print(f"  FAIL   {path.name}\n         {exc}", file=sys.stderr)
            raise
        print(f"  OK     {path.name}")
        applied += 1

    return applied


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply SQL migrations in order.")
    parser.add_argument(
        "--dry-run", action="store_true", help="list what would run, change nothing"
    )
    args = parser.parse_args(argv)

    print(f"\n  MIGRATIONS  {MIGRATIONS_DIR}")
    count = apply_all(dry_run=args.dry_run)
    if not args.dry_run:
        print(f"\n  {count} migration(s) applied\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
