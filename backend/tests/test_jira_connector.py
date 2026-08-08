"""
ASF Jira connector tests.

No network: a fake Jira over httpx.MockTransport, with payload shapes copied
from the live instance — including the ones that carry identity in places a
naive scrub walks straight past.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.ingestion.jira_connector import (
    DEFAULT_PAGE_SIZE,
    ISSUE_FIELDS,
    JiraClient,
    JiraError,
    _parse_jira_ts,
    assert_no_jira_identity,
    scrub_author,
    scrub_jira,
)


class FakeClock:
    def __init__(self) -> None:
        self.slept: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)


def user_object(handle="jsmith"):
    """A Jira user exactly as the ASF instance returns it."""
    return {
        "self": f"https://issues.apache.org/jira/rest/api/2/user?username={handle}",
        "name": handle,
        "key": handle,
        "emailAddress": f"{handle}@apache.org",
        "displayName": "J Smith",
        "active": True,
        "timeZone": "Etc/UTC",
        "avatarUrls": {"48x48": f"https://.../avatar/{handle}"},
    }


def issue(key="KAFKA-12345", updated="2026-03-01T10:00:00.000+0000"):
    return {
        "id": "13000001",
        "key": key,
        "self": "https://issues.apache.org/jira/rest/api/2/issue/13000001",
        "fields": {
            "summary": "Flaky test in ReplicaManagerTest",
            "issuetype": {"name": "Bug", "id": "1"},
            "priority": {"name": "Major", "id": "3"},
            "status": {"name": "Resolved", "id": "5"},
            "resolution": {"name": "Fixed", "id": "1"},
            "components": [{"name": "core", "id": "12310000"}],
            "parent": {"key": "KAFKA-1", "id": "12000"},
            "created": "2025-09-01T09:00:00.000+0000",
            "resolutiondate": "2026-02-01T09:00:00.000+0000",
            "updated": updated,
            "reporter": user_object("reporter1"),
            "assignee": user_object("assignee1"),
        },
        "changelog": {
            "startAt": 0,
            "maxResults": 2,
            "total": 2,
            "histories": [
                {
                    "id": "1",
                    "created": "2025-10-01T09:00:00.000+0000",
                    "author": user_object("jsmith"),
                    "items": [
                        {
                            "field": "status",
                            "fieldtype": "jira",
                            "from": "1",
                            "fromString": "Open",
                            "to": "3",
                            "toString": "Patch Available",
                        }
                    ],
                },
                {
                    "id": "2",
                    "created": "2019-01-01T09:00:00.000+0000",
                    "author": user_object("olddev"),
                    "items": [
                        {
                            "field": "assignee",
                            "fieldtype": "jira",
                            "from": "olddev",
                            "fromString": "Old Developer",
                            "to": "jsmith",
                            "toString": "J Smith",
                        }
                    ],
                },
            ],
        },
    }


# --- identity ------------------------------------------------------------


def test_user_object_becomes_an_actor_hash():
    scrubbed = scrub_author(user_object())
    assert scrubbed["actor_hash"]
    for leaked in ("name", "key", "emailAddress", "displayName", "avatarUrls"):
        assert leaked not in scrubbed


def test_jira_user_hashes_to_the_same_actor_as_the_git_author():
    """jsmith@apache.org in git and jsmith in Jira are ONE person. Two hashes
    would double the actor count and weaken the k floor."""
    from app.ingestion.git_local import identity_key_from_email
    from app.ingestion.pseudonymize import actor_hash

    git_hash = actor_hash(identity_key_from_email("jsmith@apache.org", "J Smith"))
    assert scrub_author(user_object("jsmith"))["actor_hash"] == git_hash


def test_assignee_transition_values_are_hashed():
    """THE SUBTLE ONE. An assignee changelog entry carries a username and a
    real display name with no user object anywhere in it."""
    body = scrub_jira(issue())
    item = body["changelog"]["histories"][1]["items"][0]
    assert item["fromString"] != "Old Developer"
    assert item["toString"] != "J Smith"
    assert item["from"] != "olddev"


def test_status_transition_strings_are_preserved():
    """Status names are the process vocabulary — hashing them would destroy
    the state machine the whole product is built on."""
    body = scrub_jira(issue())
    item = body["changelog"]["histories"][0]["items"][0]
    assert item["fromString"] == "Open"
    assert item["toString"] == "Patch Available"


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (("fields", "status", "name"), "Resolved"),
        (("fields", "priority", "name"), "Major"),
        (("fields", "issuetype", "name"), "Bug"),
        (("fields", "resolution", "name"), "Fixed"),
    ],
)
def test_domain_names_survive_scrubbing(path, expected):
    """Jira uses `name` for statuses and priorities as well as for people.
    Banning the key outright would strip the mapper's vocabulary."""
    body = scrub_jira(issue())
    for part in path:
        body = body[part]
    assert body == expected


def test_parent_key_survives():
    """`key` is a person in a user object and an ISSUE elsewhere."""
    assert scrub_jira(issue())["fields"]["parent"]["key"] == "KAFKA-1"


def test_component_names_survive():
    assert scrub_jira(issue())["fields"]["components"][0]["name"] == "core"


def test_no_identity_anywhere_in_a_scrubbed_issue():
    body = scrub_jira(issue())
    assert_no_jira_identity(body)
    blob = json.dumps(body)
    for leak in ("jsmith", "J Smith", "@apache.org", "Old Developer", "olddev"):
        assert leak not in blob


def test_guard_catches_an_email_in_a_string_value():
    """Keys can be clean while the value is a person."""
    with pytest.raises(AssertionError, match="email-shaped"):
        assert_no_jira_identity({"toString": "someone@apache.org"})


def test_guard_catches_a_display_name_key():
    with pytest.raises(AssertionError, match="identity key"):
        assert_no_jira_identity({"a": [{"displayName": "J Smith"}]})


def test_self_urls_are_dropped_because_they_embed_usernames():
    body = scrub_jira(issue())
    assert "self" not in body
    assert "self" not in body["changelog"]["histories"][0]


# --- timestamps ----------------------------------------------------------


def test_parses_jira_server_offset_without_a_colon():
    """Jira Server returns +0000, which fromisoformat rejects on older
    Pythons and which strptime handles."""
    ts = _parse_jira_ts("2026-03-01T10:00:00.000+0000")
    assert ts.year == 2026 and ts.tzinfo is not None


# --- client politeness ---------------------------------------------------


def test_connection_errors_are_retried():
    """The DOCUMENTED failure is 429/5xx. The MEASURED failure on ASF is the
    connection being closed — three in a row before the first success."""
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectError("connection forcibly closed")
        return httpx.Response(200, json={"total": 0, "issues": []})

    clock = FakeClock()
    client = JiraClient(
        "https://jira.example",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=clock.sleep,
    )
    client.get("/rest/api/2/search", {})
    assert calls["n"] == 3
    assert client.retries == 2


def test_429_is_retried_and_honours_retry_after():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"retry-after": "5"}, json={})
        return httpx.Response(200, json={"total": 0, "issues": []})

    clock = FakeClock()
    client = JiraClient(
        "https://jira.example",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=clock.sleep,
    )
    client.get("/rest/api/2/search", {})
    assert 5.0 in clock.slept


def test_requests_are_spaced():
    def handler(request):
        return httpx.Response(200, json={"total": 0, "issues": []})

    clock = FakeClock()
    client = JiraClient(
        "https://jira.example",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        interval_s=2.0,
        sleep=clock.sleep,
    )
    client.get("/a", {})
    client.get("/b", {})
    assert any(s > 0 for s in clock.slept), "requests were not spaced"


def test_a_404_fails_fast_with_a_useful_message():
    """A wrong project key must not burn six retries against ASF."""

    def handler(request):
        return httpx.Response(404, json={})

    client = JiraClient(
        "https://jira.example",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=FakeClock().sleep,
    )
    with pytest.raises(JiraError, match="project key"):
        client.get("/rest/api/2/issue/NOPE-1/changelog", {})


def test_no_auth_header_is_ever_sent():
    """ASF read is anonymous. Sending credentials would be both wrong and rude."""
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        seen["ua"] = request.headers.get("user-agent")
        return httpx.Response(200, json={"total": 0, "issues": []})

    client = JiraClient(
        "https://jira.example",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=FakeClock().sleep,
    )
    client.get("/rest/api/2/search", {})
    assert seen["auth"] is None
    assert "engineering-spend-intelligence" in seen["ua"]


# --- query shape ---------------------------------------------------------


def test_updated_is_requested_so_change_detection_is_possible():
    """Not in the plan's field list, but required by its own idempotency rule."""
    assert "updated" in ISSUE_FIELDS


def test_page_size_is_below_the_size_asf_refuses():
    """100 issues with inline changelogs was refused by the live instance."""
    assert DEFAULT_PAGE_SIZE <= 50
