"""Which Jira project belongs to which repository, and what a real key is.

One definition, imported by both the git connector and the normaliser. Two
copies of this drifting apart would show up as cases that exist in one lane
and not the other, which is the worst kind of bug to find on a Sunday.
"""

from __future__ import annotations

import re

#: Jira project -> the repository its issues describe.
PROJECT_TO_REPO: dict[str, str] = {
    "KAFKA": "apache/kafka",
    "FLINK": "apache/flink",
}
REPO_TO_PROJECT: dict[str, str] = {v: k for k, v in PROJECT_TO_REPO.items()}

#: A ticket key anywhere in a string, not anchored. Commit subjects follow a
#: convention and can be anchored; PR titles, branch names and bodies cannot.
TICKET_ANYWHERE = re.compile(r"(?<![A-Za-z0-9])([A-Z]{2,10}-\d+)(?![0-9])")


def is_real_ticket(key: str, repo: str) -> bool:
    """Is this UPPER-123 string actually a Jira issue in this repo's project?

    IT USUALLY IS NOT, AND THE FAILURE IS SILENT AND UGLY.

    `[A-Z]{2,10}-\\d+` matches far more than Jira keys. Measured across 4,214
    apache PR payloads it also matches KIP-909 (a Kafka Improvement Proposal —
    a design document, not an issue), BP-2, FLIP-187, CVE-2026, GHSA-72,
    CWE-287, CALCITE-7594, HADOOP-19866 — and SHA-256, UTF-8 and GPT-5.

    Unfiltered, every PR whose body mentions SHA-256 is filed into a single
    case called `SHA-256`, spanning two repositories and three years. That is
    not cosmetic: it is unrelated work glued into one case, and it surfaced as
    2,284 events timestamped before their own case had opened.

    Requiring the project to be the one belonging to this repo is also the
    "Kafka and Flink cases cannot collide" rule, enforced where cases are
    NAMED rather than by a repo filter someone has to remember to write.
    """
    project, _, number = key.partition("-")
    return bool(number) and REPO_TO_PROJECT.get(repo) == project
