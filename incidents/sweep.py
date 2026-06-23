"""Hermes incident watcher — hourly sweep (Phase 0).

Detects failures from signals Hermes already produces and prints one brief per
NEW incident to stdout. Designed to run as a Hermes ``no_agent`` cron job whose
stdout is delivered to the incidents Telegram thread.

Signals:
  * Failed cron jobs — the scheduler records ``last_error`` / ``last_delivery_error``
    (+ ``last_run_at``) on each job record.
  * Errored Langfuse traces — best-effort via the public read API (ERROR-level
    observations grouped by trace). Degrades to nothing if the API/keys are absent.

Output behaviour (matches the configured policy):
  * new incidents found            -> print brief(s)   (delivered)
  * nothing found                  -> print nothing     (cron treats empty stdout as silent)
  * nothing found AND >24h silent  -> print one "all clean" heartbeat + reset the clock

State: ``$HERMES_HOME/incidents/state.json`` -> {"seen": [...], "last_heartbeat_at": iso}
Dedup is by stable incident id, so a failure is reported once (until it recurs at
a new run), and the heartbeat clock resets on any output.

CLI:
    python -m incidents.sweep            # normal sweep
    python -m incidents.sweep --dry-run  # detect + print, do NOT touch state
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

HEARTBEAT_HOURS = 24
CRON_FAILURE_WINDOW_HOURS = 26  # a failure stays "current" until the job runs again
LANGFUSE_WINDOW_HOURS = 2
_SEEN_CAP = 2000
_BLOCKED_CAP = 500  # cap on retained blocked-commit signal lines


@dataclass
class Incident:
    id: str          # stable dedup key
    kind: str        # "cron" | "langfuse"
    title: str
    detail: str
    handoff: str     # how to hand it to Claude Code for a proposed fix


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _state_path() -> Path:
    from hermes_constants import get_hermes_home
    return get_hermes_home() / "incidents" / "state.json"


def _blocked_path() -> Path:
    from hermes_constants import get_hermes_home
    return get_hermes_home() / "incidents" / "blocked-commits.jsonl"


def _load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts


def _within(iso: Optional[str], hours: int, now: datetime) -> bool:
    ts = _parse_iso(iso)
    return ts is not None and (now - ts) <= timedelta(hours=hours)


def cron_failure_incidents(jobs: List[dict], *, now: Optional[datetime] = None,
                           window_hours: int = CRON_FAILURE_WINDOW_HOURS) -> List[Incident]:
    """Flag jobs whose most recent run recorded an error within the window."""
    now = now or _now()
    out: List[Incident] = []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        err = job.get("last_error") or job.get("last_delivery_error")
        if not err:
            continue
        last_run = job.get("last_run_at")
        if not _within(last_run, window_hours, now):
            continue
        jid = str(job.get("id") or job.get("name") or "unknown")
        err_kind = "agent error" if job.get("last_error") else "delivery error"
        out.append(Incident(
            id=f"cron:{jid}:{last_run}",
            kind="cron",
            title=f"Cron job '{job.get('name') or jid}' failed ({err_kind})",
            detail=f"when: {last_run}\nerror: {str(err)[:500]}",
            handoff=f"cron job id {jid}",
        ))
    return out


def langfuse_error_incidents(*, now: Optional[datetime] = None,
                             window_hours: int = LANGFUSE_WINDOW_HOURS) -> List[Incident]:
    """Best-effort: ERROR-level Langfuse observations grouped by trace.

    Returns [] on any problem (missing keys, network, schema) — the cron signal
    carries Phase 0 on its own. Refine against the live API as real error traces
    appear.
    """
    now = now or _now()
    pub = (os.environ.get("HERMES_LANGFUSE_PUBLIC_KEY") or os.environ.get("LANGFUSE_PUBLIC_KEY") or "").strip()
    sec = (os.environ.get("HERMES_LANGFUSE_SECRET_KEY") or os.environ.get("LANGFUSE_SECRET_KEY") or "").strip()
    base = (os.environ.get("HERMES_LANGFUSE_BASE_URL") or os.environ.get("LANGFUSE_BASE_URL") or "https://cloud.langfuse.com").strip().rstrip("/")
    if not (pub and sec):
        return []

    import base64
    import urllib.request

    frm = (now - timedelta(hours=window_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    url = f"{base}/api/public/observations?level=ERROR&fromStartTime={frm}&limit=50"
    token = base64.b64encode(f"{pub}:{sec}".encode()).decode()
    req = urllib.request.Request(url, headers={"Authorization": f"Basic {token}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 (trusted Langfuse host)
            payload = json.loads(resp.read().decode())
    except Exception:
        return []

    by_trace: dict[str, dict] = {}
    for obs in (payload.get("data") or []):
        tid = obs.get("traceId")
        if tid and tid not in by_trace:
            by_trace[tid] = obs

    out: List[Incident] = []
    for tid, obs in by_trace.items():
        msg = obs.get("statusMessage") or obs.get("name") or "error-level observation"
        out.append(Incident(
            id=f"trace:{tid}",
            kind="langfuse",
            title=f"Langfuse error trace {tid[:12]}…",
            detail=f"signal: {str(msg)[:300]}",
            handoff=f"trace-id {tid}",
        ))
    return out


def blocked_commit_incidents(path: Optional[Path] = None) -> List[Incident]:
    """Read the git-guard signal file and surface each blocked agent commit.

    The managed pre-commit hook (scripts/git-guard/pre-commit) appends one JSON
    line per blocked commit to ``$HERMES_HOME/incidents/blocked-commits.jsonl``.
    This is the alert path for the 2026-06-22 cover-wipe class: the commit is
    blocked locally AND reported here so the failure is visible, not silent.

    Best-effort: a malformed or missing file yields []. Dedup is by the existing
    seen-state (stable id per signal), so a block is reported once.
    """
    path = path or _blocked_path()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []

    out: List[Incident] = []
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except Exception:
            continue
        ts = str(rec.get("ts") or "unknown")
        repo = str(rec.get("repo") or rec.get("cwd") or "unknown")
        reason = str(rec.get("reason") or "agent commit blocked by git guard")
        cwd = str(rec.get("cwd") or "")
        out.append(Incident(
            id=f"blocked:{ts}:{cwd}:{reason[:40]}",
            kind="blocked_commit",
            title="Blocked agent commit (git guard)",
            detail=f"when: {ts}\nrepo: {repo}\nreason: {reason}",
            handoff=f"blocked commit in {repo} — review what the agent tried to delete/break",
        ))
    return out


def _prune_blocked(path: Path, cap: int = _BLOCKED_CAP) -> None:
    """Keep the signal file bounded. Reported ids persist in seen-state, so
    trimming the oldest lines never re-surfaces an already-reported block."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return
    if len(lines) <= cap:
        return
    try:
        path.write_text("\n".join(lines[-cap:]) + "\n", encoding="utf-8")
    except Exception:
        pass


def _format_brief(inc: Incident) -> str:
    return (
        f"🔴 *Incident* — {inc.title}\n"
        f"{inc.detail}\n"
        f"_To get a proposed fix, hand this to Claude Code: {inc.handoff}_"
    )


def _heartbeat_line(now: datetime) -> str:
    return (
        f"✅ Hermes incident watcher: still running, no new incidents in the last "
        f"{HEARTBEAT_HOURS}h (as of {now.strftime('%Y-%m-%d %H:%M UTC')})."
    )


def _heartbeat_due(last_hb: Optional[str], now: datetime) -> bool:
    ts = _parse_iso(last_hb)
    return ts is None or (now - ts) >= timedelta(hours=HEARTBEAT_HOURS)


def sweep(*, now: Optional[datetime] = None, jobs: Optional[List[dict]] = None,
          langfuse: Optional[List[Incident]] = None,
          blocked: Optional[List[Incident]] = None, state_path: Optional[Path] = None,
          dry_run: bool = False) -> str:
    """Run one sweep. Returns the text to deliver ("" = stay silent)."""
    now = now or _now()
    state_path = state_path or _state_path()
    state = _load_state(state_path)
    seen_list: list = list(state.get("seen", []))
    seen = set(seen_list)
    last_hb = state.get("last_heartbeat_at")

    if jobs is None:
        try:
            from cron.jobs import load_jobs
            jobs = load_jobs()
        except Exception:
            jobs = []
    lf = langfuse if langfuse is not None else langfuse_error_incidents(now=now)
    bc = blocked if blocked is not None else blocked_commit_incidents()

    incidents = cron_failure_incidents(jobs, now=now) + list(lf) + list(bc)
    new = [i for i in incidents if i.id not in seen]

    output = ""
    if new:
        output = "\n\n".join(_format_brief(i) for i in new)
        for i in new:
            seen.add(i.id)
            seen_list.append(i.id)
        last_hb = now.isoformat()
    elif _heartbeat_due(last_hb, now):
        output = _heartbeat_line(now)
        last_hb = now.isoformat()

    if not dry_run and output:
        state["seen"] = seen_list[-_SEEN_CAP:]
        state["last_heartbeat_at"] = last_hb
        _save_state(state_path, state)
    if not dry_run and blocked is None:
        _prune_blocked(_blocked_path())
    return output


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m incidents.sweep")
    parser.add_argument("--dry-run", action="store_true",
                        help="detect + print, do NOT update state")
    args = parser.parse_args(argv)
    out = sweep(dry_run=args.dry_run)
    if out:
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
