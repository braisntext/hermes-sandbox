"""Remediation class registry (Phase 1).

A remediation *class* is a known, bounded failure-and-fix pattern: a matcher that
recognises the failure from the incident the watcher already produces, a bounded
fix, an optional reversal, and a default lifecycle mode. The registry is code
(version-controlled, deploys via the §6 clone pull); per-class *mode* is runtime
state on the volume (see ``remediation/modes.py``) so a promotion survives reboot
and is never clobbered by a resync.

Signature = the incident's own ``id`` (e.g. ``cron:<jid>:<last_run>``), which the
watcher already mints stable-per-occurrence. The debounce/ledger machinery keys
off it directly — Phase 1 invents no new signature scheme.

Tier-0 admission rule: a class is eligible only if its fix is **reversible OR
bounded-and-idempotent** (re-running it cannot compound damage). A retry has no
meaningful "undo" but is idempotent at our layer — the debounce guard ensures one
fix per failure occurrence, and the job's own guards (auditor, git-guard) bound
what a re-run can touch. Such a class declares ``reversal=None`` with a rationale.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from incidents.sweep import Incident

# Substrings that mark a failure as *transient* (safe to retry). Conservative
# allowlist: if NONE is present the failure is treated as non-transient and the
# class does NOT match — it stays a human-handled incident. Better to under-fix
# (escalate to Brais) than to retry a deterministic failure forever.
_TRANSIENT_MARKERS = (
    "provider returned error",   # owl-alpha upstream 5xx (the first real incident)
    "rate limit", "rate-limit", "429",
    "timeout", "timed out", "timed-out",
    "temporarily", "temporary failure",
    "connection reset", "connection refused", "connection error",
    "502", "503", "504", "bad gateway", "service unavailable", "gateway timeout",
    "read timed out", "remote end closed",
)

# Substrings that VETO a retry even if a transient marker is also present — these
# are deterministic faults a retry can never clear (config, auth, missing code).
_HARD_FAULT_MARKERS = (
    "modulenotfound", "no module named", "no models provided",
    "401", "403", "unauthorized", "forbidden", "permission denied",
    "not found", "no such file", "invalid", "traceback",
)


def _looks_transient(text: str) -> bool:
    low = (text or "").lower()
    if any(m in low for m in _HARD_FAULT_MARKERS):
        return False
    return any(m in low for m in _TRANSIENT_MARKERS)


def _cron_job_id(inc: Incident) -> Optional[str]:
    """Extract the job id the watcher embedded in ``handoff`` ("cron job id <jid>").
    Robust to ids/names containing colons (unlike splitting ``inc.id``)."""
    prefix = "cron job id "
    if inc.handoff and inc.handoff.startswith(prefix):
        jid = inc.handoff[len(prefix):].strip()
        return jid or None
    return None


# --- fixes ------------------------------------------------------------------
# Each fix returns (ok, detail). Fixes are invoked ONLY via an explicit, approved
# `remediate apply` (Phase 1, gated) or a promoted auto-act past the guards
# (Phase 3) — never on import.

def _retry_cron(inc: Incident) -> Tuple[bool, str]:
    """Bounded fix: re-schedule the failed job for the next scheduler tick."""
    jid = _cron_job_id(inc)
    if not jid:
        return False, "could not resolve job id from incident"
    from cron.jobs import trigger_job
    job = trigger_job(jid)
    if job is None:
        return False, f"job '{jid}' not found"
    return True, f"re-scheduled job '{jid}' for next tick"


@dataclass(frozen=True)
class RemediationClass:
    name: str
    matches: Callable[[Incident], bool]       # does this incident belong here?
    fix: Callable[[Incident], Tuple[bool, str]]
    proposal: Callable[[Incident], str]       # human-readable action for the gated brief
    default_mode: str                          # "gated" | "auto"
    reversal: Optional[Callable[[Incident], Tuple[bool, str]]] = None
    rationale: str = ""                        # why Tier-0 (reversible/idempotent)


CRON_TRANSIENT_FAILURE = RemediationClass(
    name="cron-transient-failure",
    matches=lambda inc: inc.kind == "cron" and _looks_transient(inc.detail),
    fix=_retry_cron,
    proposal=lambda inc: (
        f"retry cron job ({_cron_job_id(inc) or 'unknown'}) — transient error, "
        f"idempotent re-run, no reversal needed"
    ),
    default_mode="gated",
    reversal=None,
    rationale="bounded idempotent re-run; debounce caps it to one retry per failure occurrence",
)

# Registry order = match priority (first match wins in `classify`).
REGISTRY: List[RemediationClass] = [
    CRON_TRANSIENT_FAILURE,
]


def classify(inc: Incident) -> Optional[RemediationClass]:
    """Return the first remediation class that recognises this incident, or None
    (None = no known bounded fix → it stays a plain human-handled incident)."""
    for rc in REGISTRY:
        try:
            if rc.matches(inc):
                return rc
        except Exception:
            continue
    return None
