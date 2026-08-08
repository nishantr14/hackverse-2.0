"""
Review latency waste detector.
Owner: Diljit (waste lane).
Phase: Tier 1.

Measures time between review_requested and review/merge, prices the idle
gap using cost_event.
"""

# TODO: query event_log for review_requested -> review/merge gaps, price
# via cost_event.
