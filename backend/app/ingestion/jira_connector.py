"""
Apache Jira connector — real data.
Owner: Nishant (ingestion lane).
Phase: Tier 0.

Reads issues.apache.org/jira anonymously via REST (GET/search, no auth
token needed) for the KAFKA and CASSANDRA projects. Join key: Apache's own
commit convention prefixes commits with the ticket key (e.g.
"KAFKA-16234: ..."), so work_item linkage is exact string extraction, not
fuzzy matching. Do not build a fuzzy-match fallback — it isn't needed here.
"""

# TODO: fetch issues via ASF Jira REST search API, map into work_item rows
# per docs/schema.sql, extract ticket key from commit messages for linkage.
