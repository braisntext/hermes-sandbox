"""Approval CLI for the self-remediation loop (Phase 1).

The CEO is the execution trigger for ``gated`` classes. Mirrors the
``python -m incidents.sweep`` precedent — standalone, no changes to the monolith
``cli.py``.

    python -m remediation.cli list             # pending proposals (debounce-filtered)
    python -m remediation.cli apply <signature> # approve + run one bounded fix

``apply`` re-validates against LIVE incidents before acting: if the failure has
already cleared, it does nothing (never retries a job that recovered on its own).
The ledger records the ``applied`` action + its immediate outcome; the next
watcher tick (Phase 2) verifies whether the signature actually cleared.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from remediation import ledger
from remediation.registry import RemediationClass, classify


def _live_incidents(jobs: Optional[List[dict]] = None):
    """Current cron-failure incidents — the same signal the watcher detects."""
    from incidents.sweep import cron_failure_incidents
    if jobs is None:
        try:
            from cron.jobs import load_jobs
            jobs = load_jobs()
        except Exception:
            jobs = []
    return cron_failure_incidents(jobs)


def _pending(jobs: Optional[List[dict]], *, ledger_path: Optional[Path],
             now: Optional[datetime]) -> List[Tuple[object, RemediationClass]]:
    """Live incidents that map to a remediation class and have NOT already been
    acted on for this occurrence (debounce)."""
    entries = ledger.read(path=ledger_path)
    out = []
    for inc in _live_incidents(jobs):
        rc = classify(inc)
        if rc is None:
            continue
        if ledger.recently_acted(inc.id, entries=entries, now=now):
            continue
        out.append((inc, rc))
    return out


def cmd_list(*, jobs: Optional[List[dict]] = None, ledger_path: Optional[Path] = None,
             now: Optional[datetime] = None) -> str:
    pending = _pending(jobs, ledger_path=ledger_path, now=now)
    if not pending:
        return "No pending remediation proposals."
    lines = ["Pending remediation proposals:"]
    for inc, rc in pending:
        lines.append(f"  [{rc.name}] {rc.proposal(inc)}")
        lines.append(f"      approve: python -m remediation.cli apply {inc.id}")
    return "\n".join(lines)


def cmd_apply(signature: str, *, jobs: Optional[List[dict]] = None,
              ledger_path: Optional[Path] = None, now: Optional[datetime] = None) -> Tuple[int, str]:
    """Approve + run the bounded fix for one signature. Returns (exit_code, message)."""
    inc = next((i for i in _live_incidents(jobs) if i.id == signature), None)
    if inc is None:
        return 0, (f"No current failure matches signature '{signature}' — "
                   f"already resolved? Nothing to do.")

    rc = classify(inc)
    if rc is None:
        return 1, f"No remediation class recognises incident '{signature}'."

    # Debounce: one fix per failure occurrence. A genuinely new failure mints a
    # new signature, so this only blocks re-firing the SAME occurrence.
    entries = ledger.read(path=ledger_path)
    if ledger.recently_acted(signature, entries=entries, now=now):
        return 1, (f"Already applied a fix for '{signature}' within the debounce "
                   f"window. If it persists, the watcher will escalate.")

    ok, detail = rc.fix(inc)
    outcome = ledger.OUTCOME_SUCCESS if ok else ledger.OUTCOME_FAILURE
    ledger.append(ledger.make_entry(
        rc.name, signature, inc.handoff, "gated", ledger.EVENT_APPLIED,
        outcome=outcome, detail=detail, now=now,
    ), path=ledger_path)
    status = "applied" if ok else "FAILED"
    return (0 if ok else 1), f"[{rc.name}] fix {status}: {detail}"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m remediation.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="show pending remediation proposals")
    ap = sub.add_parser("apply", help="approve + run a bounded fix for a signature")
    ap.add_argument("signature", help="the incident signature (id) to remediate")
    args = parser.parse_args(argv)

    if args.command == "list":
        print(cmd_list())
        return 0
    if args.command == "apply":
        code, msg = cmd_apply(args.signature)
        print(msg)
        return code
    return 2


if __name__ == "__main__":
    sys.exit(main())
