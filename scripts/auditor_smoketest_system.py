"""Auditor gate — system-tier smoke test (intentional, safe no-op).

Opened to validate Phase 3 of the auditor gate: that a *system*-tier PR is NOT
auto-merged even when it is clean. This file lives under ``scripts/`` and is a
``.py``, so ``auditor/tiers.py`` classifies the PR as **system**.

Expected behaviour: the auditor reviews this with the strong (system) model and,
because it is clean, posts an APPROVE comment ("I would merge this") — but it does
NOT merge, because system-tier auto-merge is intentionally still off in this
staged rollout. The CEO merges system PRs by hand. No incidents-thread ping.

It defines no logic and changes no runtime behaviour; importing it does nothing.
Safe to close without merging once the test has been observed.
"""

# Intentionally empty: this module exists only as a system-tier change for the
# auditor smoke test. There is nothing to execute and nothing to test.
