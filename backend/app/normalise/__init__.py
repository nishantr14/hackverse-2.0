"""Canonical event log: raw_payload -> event_log.

Pure functions from landed payloads to rows. No network calls anywhere in this
package — a mapping bug costs a 20-second re-run, not a 40-minute re-fetch.
"""
