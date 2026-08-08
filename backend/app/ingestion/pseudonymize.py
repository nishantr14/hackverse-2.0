"""
Actor pseudonymization — privacy by design, from the first commit ingested.
Owner: Nishant (ingestion lane).
Phase: Tier 0. This module must exist and be wired in before any real
GitHub/Jira row is written — privacy is not retrofitted.

actor_hash = sha256(login + salt). The login -> hash mapping table is kept
physically outside the analytics DB (never in Postgres alongside
event_log/cost_event). See the privacy rules in .claude/CLAUDE.md.
"""

# TODO: sha256(login + salt) hashing, mapping table written to a store
# outside the analytics DB (e.g. a separate local file/DB, not committed).
