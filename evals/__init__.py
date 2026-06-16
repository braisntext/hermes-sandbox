"""Self-repair eval loop — Phase A PoC.

A small harness that runs plain-English assertions against Hermes behaviour and,
on failure, asks a Hermes sub-agent to propose a fix. Built on the existing
Langfuse tracing plugin. Inspired by Opik/Ollie ("Your Agent Harness Should
Repair Itself").

Loop: trace -> judge -> diagnose -> human-approved diff -> verify -> regression-lock.

Entry point: ``python -m evals.run <case_name>`` (see ``evals/README.md``).
"""
