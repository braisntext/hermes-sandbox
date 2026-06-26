"""Hermes self-remediation loop — Phase 0 (foundation: ledger + guards).

Closes the Decide->Act link in the autonomy loop: lets the incident watcher
*fix* known reversible failure classes, governed by a hybrid ledger (track
record recommends promotion; the CEO approves each tier bump).

Lifecycle per class:  gated --(K clean, CEO approves each run)--> auto

Phase 0 ships ONLY the brakes, before any engine exists:
  * ``remediation/ledger.py`` — append-only JSONL record of every detection /
    action / outcome (the supervision substrate), plus the queries the guards
    need (per-signature debounce, per-class rate counting).
  * ``remediation/guards.py`` — the ``HERMES_AUTONOMY`` kill switch and
    ``may_auto_act()``, the single gate Phase 3 must pass before any auto-act.

No watcher behaviour changes here. See ``tasks/self-remediation-loop.md``.
"""
