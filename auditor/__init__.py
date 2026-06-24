"""Hermes auditor — second-LLM technical review gate for pull requests.

A dedicated ``auditor`` profile runs an agent cron that polls open PRs, reviews
each diff, and (once trusted) merges or escalates. This package holds the thin,
testable helpers the agent leans on:

  * ``tiers``   — classify a PR's changed files as ``system`` | ``content``.
  * ``pending`` — list open PRs not yet reviewed at their current head SHA
                  (dedup via ``$HERMES_HOME/auditor/state.json``).

The review judgement itself lives in the agent prompt + the auditor SOUL.md
rubric, not here. These helpers only decide *what* to review and *how hard*.
"""
