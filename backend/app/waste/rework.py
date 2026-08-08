"""
Rework detector.
Owner: Diljit (waste lane).
Phase: Tier 1.

Detects changes_requested -> commit -> review loops on the same work_item
and prices the wasted cycles using cost_event.
"""

# TODO: query event_log for rework loops per work_item, join cost_event
# for the priced waste figure.
