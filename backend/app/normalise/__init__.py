"""
Normalisation — pure mapping from `raw_payload` to `work_item` and `event_log`.
Owner: Dipen (normalise lane).

Nothing in this package makes a network call. Every module here is a function
from rows already landed in `raw_payload` to rows in the analytics tables, so a
mapping bug costs a re-run rather than a re-fetch.
"""
