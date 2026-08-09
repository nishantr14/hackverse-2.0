"""
Local git extraction — the unblocker. No API calls, no token, no rate limit.
Owner: Nishant (ingestion lane).
Phase: Tier 0.

    python -m app.ingestion.git_local --repo apache/kafka
    python -m app.ingestion.git_local            # every repo in GITHUB_REPOS

Clones each repo shallow, reads `git log --numstat`, lands the parsed commits
in `raw_payload`, then maps them to `actor`, `work_item` and `event_log`.
Idempotent: re-running writes the same rows and changes no counts.

WHY NOT --filter=blob:none
--------------------------
The plan called for a blobless partial clone. Measured on apache/kafka, 1,844
commits over 12 months:

    --filter=blob:none + --numstat    clone 10s, extract ~44 min
    plain --shallow-since + --numstat clone 18s, extract 7.1s, 33 MB on disk

`--numstat` counts changed lines, so it needs blob content — precisely what the
filter omits. Every commit then round-trips to GitHub to refetch what the
filter skipped, which is both slower than a full clone and heavier on the
network. The filter is removed and `--shallow-since` does the size work
instead.

LINES OF CODE ARE NOT EFFORT
----------------------------
`additions`/`deletions` are recorded in `attrs` as descriptive metadata and are
used by nothing. Effort comes from session inference over event timestamps
(hard rule #1). If you are about to write `additions + deletions` as a measure
of work, stop and read .claude/CLAUDE.md.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import bindparam, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import REPO_ROOT, get_settings
from app.db.models import Actor, EventLog, RawPayload, WorkItem
from app.db.session import write_session
from app.ingestion.projects import is_real_ticket
from app.ingestion.pseudonymize import (
    IdentityStore,
    actor_hash,
    assert_no_identity,
    identity_store,
    is_bot,
    warn_if_default_salt,
)

logger = logging.getLogger(__name__)

SOURCE = "git_local"
CLONE_ROOT = REPO_ROOT / "data" / "repos"

#: Sentinel prefix marking a commit header line. Chosen because it cannot begin
#: a numstat line, which always starts with a digit or '-'.
HEADER = "@@@"

#: sha, author date, COMMIT date, author email, author name, parents, subject.
#: Subject is LAST because it is the only field that may itself contain '|'.
PRETTY = f"{HEADER}%H|%aI|%cI|%aE|%aN|%P|%s"

#: AUTHOR DATE vs COMMIT DATE — this bites, and it bit us.
#:
#: `event_log.ts` is the AUTHOR date: when the person actually did the work,
#: which is what session inference must measure. But `git log --since` filters
#: on the COMMIT date, so a patch authored in March 2024 and merged in 2026
#: passes a "12 months ago" filter carrying a 2024 timestamp. Apache Flink's
#: patch-based workflow does this constantly: 39 of 1,264 commits landed
#: outside the window and stretched the global sprint grid from 26 windows to
#: 63, leaving 37 of them nearly empty.
#:
#: That is not cosmetic. `work_item.sprint` is the axis Diljit's spend-by-sprint
#: view and Dipen's time-based train/test split both key off. So we re-filter on
#: the author date after parsing, and keep the commit date in `attrs` because it
#: is the honest answer to "when did this enter the repository".
COMMIT_DATE_ATTR = "committed_at"

#: Apache commit subjects are prefixed with the Jira key: "KAFKA-16234: ...".
#: Anchored to the start so a key mentioned mid-message does not steal the case.
TICKET_RE = re.compile(r"^\s*\[?([A-Z]{2,10}-\d+)\]?")

#: GitHub appends "(#23015)" to the subject when a PR is squash-merged. Measured
#: on apache/kafka, 12 months: 1,115 commits carry a ticket key, a further 721
#: carry only this, and 5 carry neither.
#:
#: This does NOT reduce the case count — Kafka squash-merges, so each ticketless
#: commit is its own PR (726 cases either way). The win is JOINABILITY: the case
#: id becomes `apache/kafka#23015`, exactly what the GitHub PR connector will
#: emit, so the review_requested / approved / merged events land on the SAME
#: case as the commit. Keyed by sha instead, those events would create a second
#: parallel case and the process graph would show coding and reviewing as two
#: disconnected islands.
#: Anchored to the END so a PR referenced mid-subject does not steal the case.
PR_RE = re.compile(r"\(#(\d+)\)\s*$")

#: Files at the repository root belong to no component.
ROOT_COMPONENT = "(root)"

# Provisional band assignment. Decision #8: rates are public and cited, band
# ASSIGNMENT is a stated rule over contribution history and is labelled
# 'inferred' everywhere. This is the placeholder rule so `actor.role_band` can
# be NOT NULL; Diljit's lane replaces it with the real one. Thresholds are
# commit counts inside the ingestion window.
BAND_THRESHOLDS: tuple[tuple[int, str], ...] = (
    (100, "staff"),
    (30, "senior"),
    (8, "mid"),
    (0, "junior"),
)


@dataclass
class Commit:
    sha: str
    authored_at: datetime
    committed_at: datetime
    identity_key: str
    subject: str
    parents: list[str]
    files: list[str] = field(default_factory=list)
    additions: int = 0
    deletions: int = 0

    @property
    def component(self) -> str | None:
        """Top-level directory of the majority of files touched.

        A commit spanning `core/` and `clients/` is attributed to whichever it
        touched more of. Ties break alphabetically so the result is stable
        across runs — a component that flickers between runs would make every
        downstream cost number irreproducible.
        """
        if not self.files:
            return None
        tops = Counter(
            path.split("/", 1)[0] if "/" in path else ROOT_COMPONENT
            for path in self.files
        )
        best = max(tops.values())
        return min(name for name, n in tops.items() if n == best)

    @property
    def ticket_key(self) -> str | None:
        match = TICKET_RE.match(self.subject)
        return match.group(1) if match else None

    @property
    def pr_number(self) -> str | None:
        match = PR_RE.search(self.subject)
        return match.group(1) if match else None


@dataclass
class Stats:
    commits_seen: int = 0
    commits_bot: int = 0
    commits_outside_window: int = 0
    raw_payload: int = 0
    actors: int = 0
    work_items: int = 0
    events: int = 0
    with_ticket_key: int = 0
    components: Counter = field(default_factory=Counter)
    bands: Counter = field(default_factory=Counter)
    case_sources: Counter = field(default_factory=Counter)


# ---------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------


def identity_key_from_email(email: str, name: str) -> str:
    """Reduce a git author to the most login-like stable key available.

    git log gives an email and a display name; the GitHub API gives a login.
    Hashing them naively produces two different `actor_hash` values for one
    person, which doubles the actor count, halves every per-actor figure, and
    — because `v_spend_by_component` counts DISTINCT actors — makes the
    k-anonymity floor LESS likely to suppress. That is a privacy regression,
    not just a cosmetic one.

    So we recover a login wherever the email encodes one:

        1234567+octocat@users.noreply.github.com -> octocat
        octocat@users.noreply.github.com         -> octocat
        jsmith@apache.org                        -> jsmith  (ASF id)
        someone@example.com                      -> someone@example.com

    The first three cover the overwhelming majority of Apache commits. The
    fallback still yields a stable pseudonym; it just will not join to a
    GitHub login until the API connector reconciles it.
    """
    handle = (email or "").strip().lower()
    if not handle:
        return (name or "").strip().lower()
    local, _, domain = handle.partition("@")
    if domain == "users.noreply.github.com":
        return local.split("+", 1)[-1]
    if domain == "apache.org":
        return local
    return handle


def infer_band(commit_count: int) -> str:
    for threshold, band in BAND_THRESHOLDS:
        if commit_count >= threshold:
            return band
    return "junior"


def infer_tenure(first_seen: datetime, latest: datetime) -> str:
    """Tenure bucket from observed activity span.

    NOTE: the ingestion window is HISTORY_MONTHS long, so `gt_2y` is not
    observable from it and is never assigned here. Anyone active across the
    whole window lands in `6m_2y`. Widening the window is the only honest way
    to populate the top bucket.
    """
    return "lt_6m" if (latest - first_seen) < timedelta(days=182) else "6m_2y"


# ---------------------------------------------------------------------
# Cloning and reading
# ---------------------------------------------------------------------


def _run_git(args: Sequence[str], cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return result.stdout


def ensure_clone(repo: str, since: str) -> Path:
    """Clone `owner/name` shallow, or fetch if it is already on disk.

    `--no-checkout` skips writing a working tree we never read, and
    `--single-branch` skips every branch but the default. See the module
    docstring for why `--filter=blob:none` is deliberately absent.
    """
    path = CLONE_ROOT / repo
    if (path / ".git").exists() or (path / "HEAD").exists():
        logger.info("fetching %s", repo)
        _run_git(["fetch", "--quiet", f"--shallow-since={since}", "origin"], cwd=path)
        return path

    path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("cloning %s (shallow since %s)", repo, since)
    _run_git(
        [
            "clone",
            "--quiet",
            "--no-checkout",
            "--single-branch",
            f"--shallow-since={since}",
            f"https://github.com/{repo}.git",
            str(path),
        ]
    )
    return path


def read_commits(path: Path, since: str) -> Iterator[Commit]:
    """Parse `git log --numstat` into Commit objects.

    Streams rather than accumulating: the log for a 12-month window of a large
    repo is tens of MB of text and there is no reason to hold it twice.
    """
    raw = _run_git(
        [
            "log",
            f"--since={since}",
            "--numstat",
            "--date=iso-strict",
            f"--pretty=format:{PRETTY}",
        ],
        cwd=path,
    )

    current: Commit | None = None
    for line in raw.splitlines():
        if line.startswith(HEADER):
            if current is not None:
                yield current
            current = _parse_header(line)
            continue
        if current is None or not line.strip():
            continue
        added, removed, filepath = _parse_numstat(line)
        if filepath is None:
            continue
        current.files.append(filepath)
        current.additions += added
        current.deletions += removed
    if current is not None:
        yield current


def _parse_header(line: str) -> Commit:
    # maxsplit=6 keeps a '|' inside the subject from shifting every field.
    sha, authored, committed, email, name, parents, subject = line[len(HEADER) :].split(
        "|", 6
    )
    return Commit(
        sha=sha,
        # %aI/%cI preserve the AUTHOR'S original tz offset (e.g. +05:30), not
        # UTC. GraphQL's authoredDate is always UTC, so two representations
        # of the same instant hashed different strings in event_id_for and
        # ~1.7% of commits landed twice under two ids. Normalize here, once,
        # rather than at every call site — the working agreement is "all
        # timestamps UTC," and this is the one place that wasn't.
        authored_at=datetime.fromisoformat(authored).astimezone(UTC),
        committed_at=datetime.fromisoformat(committed).astimezone(UTC),
        identity_key=identity_key_from_email(email, name),
        subject=subject,
        parents=parents.split() if parents.strip() else [],
    )


def _parse_numstat(line: str) -> tuple[int, int, str | None]:
    parts = line.split("\t")
    if len(parts) < 3:
        return 0, 0, None
    added_s, removed_s, filepath = parts[0], parts[1], parts[2]
    # '-' marks a binary file: git cannot count lines in it.
    added = int(added_s) if added_s.isdigit() else 0
    removed = int(removed_s) if removed_s.isdigit() else 0
    # Renames arrive as "old => new" or "dir/{a => b}/file"; take the new path.
    if " => " in filepath:
        filepath = _resolve_rename(filepath)
    return added, removed, filepath.strip()


def _resolve_rename(filepath: str) -> str:
    brace = re.search(r"\{(.*?) => (.*?)\}", filepath)
    if brace:
        return filepath[: brace.start()] + brace.group(2) + filepath[brace.end() :]
    return filepath.split(" => ", 1)[-1]


# ---------------------------------------------------------------------
# Identifiers
# ---------------------------------------------------------------------


def event_id_for(sha: str, ts: datetime) -> str:
    """Deterministic event id, so a re-run upserts instead of duplicating."""
    parts = (SOURCE, "commit", sha, "commit", ts.isoformat())
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]


def work_item_id_for(commit: Commit, repo: str) -> tuple[str, str]:
    """Case id and its provenance, per the fallback chain in decision #6.

    ticket_key -> pr -> sha placeholder. Nothing is ever dropped.

    The `pr` id is formatted `owner/name#number` so it collides deliberately
    with what the GitHub PR connector will produce: when that lands, its rows
    merge into these cases rather than creating a parallel universe of them.

    The final placeholder is recorded as `pr` because the schema's CHECK allows
    only ticket_key/issue/pr. Measured at 5 commits in 1,841 on apache/kafka,
    so the imprecision is bounded and visible in the run report.
    """
    key = commit.ticket_key
    # `KIP-909:` and `CVE-2026-...` open commit subjects here exactly as often
    # as `KAFKA-19871:` does, and neither is an issue. Unfiltered they created
    # 63 phantom cases. See ingestion.projects.is_real_ticket.
    if key and is_real_ticket(key, repo):
        return key, "ticket_key"
    pr = commit.pr_number
    if pr:
        return f"{repo}#{pr}", "pr"
    return f"{repo}@{commit.sha[:12]}", "pr"


# ---------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------


def _write_raw_payloads(session: Session, repo: str, commits: list[Commit]) -> int:
    """Land the parsed commit before mapping it.

    DEVIATION, deliberate: the body stored here is already pseudonymised. The
    "land raw, then map" rule exists so a mapping bug costs a re-run rather
    than a re-fetch, and that still holds — every field the mapper reads is
    present. What is NOT present is the author email and name, because
    raw_payload lives in Postgres and Postgres holds no identity. Re-running
    the mapper is free; re-cloning is 18 seconds. Identity in the warehouse is
    forever.
    """
    rows = [
        {
            "source": SOURCE,
            "entity_type": "commit",
            "entity_id": commit.sha,
            "body": {
                "sha": commit.sha,
                "repo": repo,
                "authored_at": commit.authored_at.isoformat(),
                "committed_at": commit.committed_at.isoformat(),
                "actor_hash": actor_hash(commit.identity_key),
                "subject": commit.subject,
                "parents": commit.parents,
                "files": commit.files,
                "additions": commit.additions,
                "deletions": commit.deletions,
            },
        }
        for commit in commits
    ]
    if not rows:
        return 0
    assert_no_identity(rows)
    for row in rows:
        assert_no_identity(row["body"])
    stmt = insert(RawPayload).values(rows)
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["source", "entity_type", "entity_id"],
            set_={"body": stmt.excluded.body},
        )
    )
    return len(rows)


def _write_actors(
    session: Session, commits: list[Commit], store: IdentityStore
) -> tuple[dict[str, str], Counter]:
    """Create one actor per distinct human, return identity_key -> actor_hash."""
    counts = Counter(c.identity_key for c in commits)
    first_seen: dict[str, datetime] = {}
    last_seen: dict[str, datetime] = {}
    for commit in commits:
        key = commit.identity_key
        if key not in first_seen or commit.authored_at < first_seen[key]:
            first_seen[key] = commit.authored_at
        if key not in last_seen or commit.authored_at > last_seen[key]:
            last_seen[key] = commit.authored_at

    mapping: dict[str, str] = {}
    rows = []
    bands: Counter = Counter()
    for key, n in counts.items():
        digest = store.record(key, source=SOURCE)
        if digest is None:  # bot slipped through; belt and braces
            continue
        mapping[key] = digest
        band = infer_band(n)
        bands[band] += 1
        rows.append(
            {
                "actor_hash": digest,
                "role_band": band,
                "tenure_bucket": infer_tenure(first_seen[key], last_seen[key]),
                "first_seen": first_seen[key],
                "band_basis": "inferred",
            }
        )
    if rows:
        assert_no_identity(rows)
        stmt = insert(Actor).values(rows)
        session.execute(stmt.on_conflict_do_nothing(index_elements=["actor_hash"]))
    return mapping, bands


def _write_work_items(session: Session, repo: str, commits: list[Commit]) -> int:
    """Provisional cases. Later sources enrich them rather than overwriting them.

    Each source updates only the columns it actually owns. `component` is
    derived from file paths, so git is authoritative for it and a re-run may
    correct it. `jira_key`, `issue_type` and the rest belong to the Jira
    connector and are set on INSERT only — otherwise a git re-run at hour 30
    would silently blank fields Jira spent 40 minutes fetching.
    """
    seen: dict[str, dict] = {}
    for commit in commits:
        work_item_id, case_source = work_item_id_for(commit, repo)
        row = seen.get(work_item_id)
        if row is None:
            seen[work_item_id] = {
                "work_item_id": work_item_id,
                "repo": repo,
                "component": commit.component,
                "case_source": case_source,
                "source_ref": commit.sha,
                "opened_at": commit.authored_at,
                "jira_key": commit.ticket_key,
            }
        elif commit.authored_at < row["opened_at"]:
            row["opened_at"] = commit.authored_at
            row["component"] = commit.component or row["component"]

    rows = list(seen.values())
    if not rows:
        return 0
    assert_no_identity(rows)
    stmt = insert(WorkItem).values(rows)
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["work_item_id"],
            set_={
                "component": stmt.excluded.component,
                # A case opened when its EARLIEST commit landed, whichever run
                # discovered that commit.
                "opened_at": func.least(WorkItem.opened_at, stmt.excluded.opened_at),
            },
        )
    )
    return len(rows)


def _write_events(
    session: Session, repo: str, commits: list[Commit], actors: dict[str, str]
) -> int:
    rows = []
    for commit in commits:
        digest = actors.get(commit.identity_key)
        if digest is None:
            continue
        work_item_id, _ = work_item_id_for(commit, repo)
        rows.append(
            {
                "event_id": event_id_for(commit.sha, commit.authored_at),
                "work_item_id": work_item_id,
                "actor_hash": digest,
                "activity": "commit",
                "ts": commit.authored_at,
                "source": "github",
                "attrs": {
                    "sha": commit.sha,
                    "parents": commit.parents,
                    COMMIT_DATE_ATTR: commit.committed_at.isoformat(),
                    "files": commit.files,
                    # Descriptive only. Never an effort proxy — hard rule #1.
                    "additions": commit.additions,
                    "deletions": commit.deletions,
                },
            }
        )
    if not rows:
        return 0
    assert_no_identity(rows)
    stmt = insert(EventLog).values(rows)
    session.execute(stmt.on_conflict_do_nothing(index_elements=["event_id"]))
    return len(rows)


def assign_sprints(session: Session, sprint_days: int) -> int:
    """Number fixed-length windows backwards from the newest event, 1..N ascending.

    Computed GLOBALLY across event_log, not per repo, and recomputed on every
    run. Decision #7: the sprint proxy is derived once so that everyone splits
    the data identically — if two lanes numbered their own windows, the
    forecaster and the spend view would disagree about which sprint a case
    belongs to and nobody would notice until stage.
    """
    bounds = session.execute(
        select(EventLog.work_item_id, EventLog.ts).order_by(EventLog.ts)
    ).all()
    if not bounds:
        return 0

    first_ts: dict[str, datetime] = {}
    for work_item_id, ts in bounds:
        if work_item_id not in first_ts:
            first_ts[work_item_id] = ts

    latest = max(ts for _, ts in bounds)
    earliest = min(ts for _, ts in bounds)
    window = timedelta(days=sprint_days)
    total = int((latest - earliest) / window) + 1

    params = [
        {"wid": work_item_id, "sprint": total - int((latest - ts) / window)}
        for work_item_id, ts in first_ts.items()
    ]
    # One executemany, not one round trip per case: there are thousands of them.
    session.execute(
        WorkItem.__table__.update()
        .where(WorkItem.work_item_id == bindparam("wid"))
        .values(sprint=bindparam("sprint")),
        params,
    )
    return total


# ---------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------


def ingest_repo(repo: str, session: Session, store: IdentityStore) -> Stats:
    settings = get_settings()
    since = f"{settings.history_months} months ago"
    path = ensure_clone(repo, since)

    # git filters on commit date; we key on author date. Re-apply the window to
    # the field we actually store, or the sprint grid inherits years of drift.
    cutoff = datetime.now(UTC) - timedelta(days=round(settings.history_months * 30.44))

    stats = Stats()
    commits: list[Commit] = []
    for commit in read_commits(path, since):
        stats.commits_seen += 1
        if commit.authored_at < cutoff:
            stats.commits_outside_window += 1
            continue
        if is_bot(commit.identity_key):
            stats.commits_bot += 1
            continue
        commits.append(commit)
        if commit.ticket_key:
            stats.with_ticket_key += 1
            stats.case_sources["ticket_key"] += 1
        elif commit.pr_number:
            stats.case_sources["pr_number"] += 1
        else:
            stats.case_sources["sha_placeholder"] += 1
        if commit.component:
            stats.components[commit.component] += 1

    stats.raw_payload = _write_raw_payloads(session, repo, commits)
    actors, stats.bands = _write_actors(session, commits, store)
    stats.actors = len(actors)
    stats.work_items = _write_work_items(session, repo, commits)
    stats.events = _write_events(session, repo, commits, actors)
    return stats


def _print_report(repo: str, stats: Stats, sprints: int) -> None:
    kept = stats.commits_seen - stats.commits_bot
    print(f"\n=== {repo} ===")
    print(f"  commits read          {stats.commits_seen}")
    print(f"  bot commits dropped   {stats.commits_bot}")
    print(
        f"  outside {get_settings().history_months}-month window "
        f"{stats.commits_outside_window} (author date predates it)"
    )
    print("\n  rows written")
    print(f"    raw_payload         {stats.raw_payload}")
    print(f"    actor               {stats.actors}")
    print(f"    work_item           {stats.work_items}")
    print(f"    event_log           {stats.events}")

    print("\n  component distribution (top 10)")
    total_c = sum(stats.components.values()) or 1
    for name, n in stats.components.most_common(10):
        print(f"    {name:<28} {n:>6}  {n / total_c:6.1%}")

    print("\n  inferred band distribution (PROVISIONAL rule — see BAND_THRESHOLDS)")
    for band in ("junior", "mid", "senior", "staff"):
        print(f"    {band:<28} {stats.bands.get(band, 0):>6}")

    print("\n  case id resolution (decision #6 fallback chain)")
    labels = {
        "ticket_key": "ticket key (joins to Jira)",
        "pr_number": "PR number from subject",
        "sha_placeholder": "sha placeholder (unmapped)",
    }
    for key, label in labels.items():
        n = stats.case_sources.get(key, 0)
        print(f"    {label:<28} {n:>6}  {n / kept if kept else 0:6.1%}")

    share = stats.with_ticket_key / kept if kept else 0.0
    unmapped = stats.case_sources.get("sha_placeholder", 0)
    print(f"\n  sprint windows (global)   {sprints}")
    print(f"  COMMITS WITH A TICKET KEY  {stats.with_ticket_key}/{kept} = {share:.1%}")
    print("  ^ this is the slide number: how much of the work log joins to Jira.")
    print(
        f"  Commits resolved to a real case: {kept - unmapped}/{kept} = "
        f"{(kept - unmapped) / kept if kept else 0:.1%}\n"
    )


def main(argv: Sequence[str] | None = None) -> int:
    warn_if_default_salt()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    settings = get_settings()

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo",
        action="append",
        help="owner/name; repeatable. Defaults to every repo in GITHUB_REPOS.",
    )
    args = parser.parse_args(argv)
    repos = args.repo or settings.github_repo_list

    with write_session() as session, identity_store() as store:
        results = [(repo, ingest_repo(repo, session, store)) for repo in repos]
        session.flush()
        sprints = assign_sprints(session, settings.sprint_days)

    for repo, stats in results:
        _print_report(repo, stats, sprints)
    return 0


if __name__ == "__main__":
    sys.exit(main())
