# Hand-written `raw_payload` fixtures

Each `pr_*.json` and `run_*.json` file is the `body` column of one
`raw_payload` row, in the shape the connector writes it — **already scrubbed**,
because `github_connector` hashes every login at the fetch boundary. That is
why you see `actor_hash` and `is_bot` here and never a `login`.

These are written by hand rather than captured from the API so that each one
isolates exactly one thing the mapper has to get right:

| file | what it pins |
|---|---|
| `pr_no_ticket_key.json` | falls through to `{repo}#{number}`, `case_source='pr'` |
| `pr_multiple_review_rounds.json` | two reviewers, three rounds, same-second approvals |
| `pr_force_push.json` | `HeadRefForcePushedEvent` -> `force_push`, plus a bot review |
| `pr_reopened_with_issue.json` | `closingIssuesReferences` -> `case_source='issue'` |
| `pr_ticket_key_repoints_commit.json` | merge commit currently on a provisional case |
| `run_matching_head_sha.json` | Actions run that joins to a case on `head_sha` |
| `run_unmatched.json` | Actions run with no matching sha — `work_item_id` stays null |

`actor_hash` values are 16 hex characters like the real ones but are obviously
fake (`aaaa...`, `bbbb...`) so nobody mistakes a fixture for production data.
