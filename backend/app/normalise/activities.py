"""The activity vocabulary, and the translation into it.

THIS FILE EXISTS BECAUSE THE BRIEF AND THE SCHEMA DISAGREE.

The Prompt 5 brief names thirteen activities. `event_log.activity` carries a
CHECK constraint listing seventeen, and six of the brief's names are not among
them: `pr_opened`, `review_submitted`, `ci_started`, `ci_completed`,
`ci_rerun`, `jira_status_changed`. Writing any of them is not a style
disagreement — Postgres rejects the INSERT.

The schema is frozen and takes four people to change, so the vocabulary wins
and the translation lives here in one auditable table rather than being
scattered as string literals across three mappers. Every requested activity is
accounted for below: mapped, or carried as an attribute, or explicitly
declined with a reason.
"""

from __future__ import annotations

from app.db.models import ACTIVITIES

#: Frozen vocabulary, imported so this module cannot drift from the schema.
CANONICAL: frozenset[str] = frozenset(ACTIVITIES)

#: Requested name -> what we actually write, and why.
#:
#: A value of None means the information is preserved somewhere other than
#: `event_log.activity`; the note says where. Nothing in the brief is dropped.
REQUESTED_TO_CANONICAL: dict[str, tuple[str | None, str]] = {
    "commit": ("commit", "identical"),
    "pr_opened": (
        None,
        (
            "No canonical activity exists. PR creation time is written to "
            "work_item.opened_at, a case attribute rather than an event — "
            "arguably where it belonged anyway, since 'the case was opened' "
            "is not something a person did inside the case. Raising pr_opened "
            "to a real activity is a schema change and needs the vote."
        ),
    ),
    "review_requested": ("review_requested", "identical"),
    "review_submitted": ("review", "decision #14: one spelling, already chosen"),
    "changes_requested": ("changes_requested", "identical"),
    "approved": ("approved", "decision #14"),
    "reopened": ("reopened", "identical"),
    "force_push": ("force_push", "identical"),
    "merged": ("merged", "decision #14"),
    "ci_started": (
        "ci_run",
        (
            "One event at run_started_at. attrs.completed_at carries the other "
            "timestamp and duration_s the span, so no information is lost and "
            "no case sequence is padded with a start/complete pair that would "
            "double the length of every variant."
        ),
    ),
    "ci_completed": ("ci_run", "same event; see attrs.completed_at"),
    "ci_rerun": (
        "ci_run",
        (
            "attrs.attempt > 1 and attrs.is_rerun. A separate activity would "
            "make a rerun invisible to any query that groups on CI, which is "
            "the opposite of what we want given reruns ARE the waste signal."
        ),
    ),
    "jira_status_changed": (
        None,
        (
            "Split by target status into ticket_started / ticket_in_review / "
            "ticket_resolved / ticket_closed / ticket_reopened. A process map "
            "reading 'jira_status_changed -> jira_status_changed -> "
            "jira_status_changed' has no process in it. attrs.from_status and "
            "attrs.to_status keep the raw transition for anyone who wants to "
            "regroup differently."
        ),
    ),
}

#: Jira status names, measured against the landed KAFKA changelogs rather than
#: assumed: Open, In Progress, Patch Available, Resolved, Reopened, Closed.
#: These six are the entire observed vocabulary.
JIRA_STATUS_TO_ACTIVITY: dict[str, str] = {
    "in progress": "ticket_started",
    "patch available": "ticket_in_review",
    "in review": "ticket_in_review",
    "reviewable": "ticket_in_review",
    "resolved": "ticket_resolved",
    "closed": "ticket_closed",
    "reopened": "ticket_reopened",
    # A transition whose TARGET is Open is a move backwards — a withdrawn
    # patch or a rejected fix — not a creation. `ticket_created` is emitted
    # once per issue from fields.created, never from a transition.
    "open": "ticket_reopened",
}

#: GitHub review states. PENDING is a draft the author has not submitted, so
#: there is no evidence any review happened; the brief's own rule says do not
#: invent events, so it is skipped.
REVIEW_STATE_TO_ACTIVITY: dict[str, str] = {
    "APPROVED": "approved",
    "CHANGES_REQUESTED": "changes_requested",
    "COMMENTED": "review",
    "DISMISSED": "review",
}

#: Timeline item __typename -> activity.
TIMELINE_TO_ACTIVITY: dict[str, str] = {
    "ReviewRequestedEvent": "review_requested",
    "ReopenedEvent": "reopened",
    "HeadRefForcePushedEvent": "force_push",
}


def canonical(requested: str) -> str | None:
    """Translate a brief-name into a writable activity, or None if it is not one."""
    return REQUESTED_TO_CANONICAL.get(requested, (requested, ""))[0]


def assert_writable(activity: str) -> str:
    """Fail here rather than at the INSERT, where the error names no field."""
    if activity not in CANONICAL:
        raise ValueError(
            f"{activity!r} is not in the frozen activity vocabulary. "
            f"Allowed: {sorted(CANONICAL)}"
        )
    return activity
