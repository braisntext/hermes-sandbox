"""List open PRs the auditor has not yet reviewed at their current head SHA.

The auditor agent cron runs this, reviews each returned PR, then records the
review with ``--mark`` so the same head isn't re-reviewed. A PR reappears here
the moment its author pushes a new commit (head SHA changes) — which is exactly
how the back-and-forth works: auditor comments, author addresses, new SHA,
auditor re-reviews.

Dedup mirrors the incident watcher (``incidents/sweep.py``): a bounded ``seen``
list of stable ids, persisted to ``$HERMES_HOME/auditor/state.json``. The id is
``"<number>@<headSha>"`` so review is keyed to the exact reviewed tree.

PR data comes from ``gh pr list`` (no GitHub Actions involved — this is plain
API polling, the only path available on a Free private account).

CLI:
    python -m auditor.pending                  # JSON array of PRs needing review
    python -m auditor.pending --repo owner/name
    python -m auditor.pending --mark 42 <sha>  # record PR #42 @ sha as reviewed
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

_SEEN_CAP = 2000
# Fields pulled per PR. ``files`` lets the agent tier without a second call.
_PR_FIELDS = "number,title,headRefName,headRefOid,author,isDraft,files,url"


def _state_path() -> Path:
    from hermes_constants import get_hermes_home

    return get_hermes_home() / "auditor" / "state.json"


def _load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def _save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _pr_id(number: int, head_sha: str) -> str:
    return f"{number}@{head_sha}"


def _gh_list_open_prs(repo: Optional[str]) -> List[dict]:
    """Return open PRs via ``gh``. Empty list if gh fails (degrade quietly)."""
    cmd = ["gh", "pr", "list", "--state", "open", "--limit", "100", "--json", _PR_FIELDS]
    if repo:
        cmd += ["--repo", repo]
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60, check=True
        ).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(f"auditor.pending: gh pr list failed: {e}", file=sys.stderr)
        return []
    try:
        return json.loads(out) or []
    except ValueError:
        return []


def pending_prs(repo: Optional[str], state_path: Path, include_drafts: bool = False) -> List[dict]:
    """Open PRs whose current head SHA has not been reviewed yet.

    Each item is the ``gh`` PR object plus a flat ``changed_files`` list (paths)
    for convenient tiering by the caller.
    """
    state = _load_state(state_path)
    seen = set(state.get("seen", []))
    result = []
    for pr in _gh_list_open_prs(repo):
        if pr.get("isDraft") and not include_drafts:
            continue
        number = pr.get("number")
        head = pr.get("headRefOid") or ""
        if number is None or not head:
            continue
        if _pr_id(number, head) in seen:
            continue
        pr["changed_files"] = [f.get("path") for f in (pr.get("files") or []) if f.get("path")]
        result.append(pr)
    return result


def mark_reviewed(number: int, head_sha: str, state_path: Path) -> None:
    """Record PR ``number`` at ``head_sha`` as reviewed (bounded ``seen`` list)."""
    state = _load_state(state_path)
    seen_list: list = list(state.get("seen", []))
    pid = _pr_id(number, head_sha)
    if pid not in seen_list:
        seen_list.append(pid)
    state["seen"] = seen_list[-_SEEN_CAP:]
    _save_state(state_path, state)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="List PRs the auditor must review.")
    ap.add_argument("--repo", help="owner/name (default: gh infers from cwd)")
    ap.add_argument("--include-drafts", action="store_true")
    ap.add_argument(
        "--mark", nargs=2, metavar=("NUMBER", "SHA"),
        help="record a PR head as reviewed instead of listing",
    )
    args = ap.parse_args(argv)
    state_path = _state_path()

    if args.mark:
        number, sha = args.mark
        mark_reviewed(int(number), sha, state_path)
        print(f"marked PR #{number} @ {sha} reviewed")
        return 0

    prs = pending_prs(args.repo, state_path, include_drafts=args.include_drafts)
    print(json.dumps(prs, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
