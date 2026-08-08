"""
Engineering Spend Intelligence — backend config.
Owner: shared infra (whoever wires a new env var, update .env.example too).
Phase: Tier 0 (must exist before anything else boots).

Env-driven settings only. Never hardcode secrets, tokens, or DB credentials
here — see ../.env.example for the full variable list.
"""

# TODO: pydantic-settings BaseSettings class reading from environment,
# matching every key in .env.example (DB URL, GITHUB_TOKEN, ASF Jira base
# URL, K_ANONYMITY_FLOOR / K_ANONYMITY_FALLBACK, session-inference gap/
# lead-in constants).
