"""
Forecaster — LightGBM quantile regression.
Owner: Dipen (models lane).
Phase: Tier 2.

Ship P50 first, then P10/P90 if time allows. This is a documented model,
not an LLM — its outputs satisfy the determinism discipline the same way
SQL/pandas does, as long as the model + features are documented.
"""

# TODO: LightGBM quantile model trained on work_item + cost_event features,
# predicting cost/duration. P50 first.
