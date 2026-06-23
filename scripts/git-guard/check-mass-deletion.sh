#!/usr/bin/env bash
#
# check-mass-deletion.sh — universal, config-free mass-deletion tripwire.
#
# Blocks a commit that stages more than N tracked-file DELETIONS, regardless of
# file type. This is the failure class behind the 2026-06-22 biglobster
# cover-wipe: commit dd0e1f5 ("commit pending changes for rebase sync") was a
# blind `git add -A && git commit` that captured the deletion of 48 cover /
# infographic images while the HTML still referenced them — pushed straight to
# main, blog served 404s for ~22h. A file-type-agnostic deletion cap makes that
# class impossible to commit by accident in any repo.
#
# This check is intentionally config-free: it needs no per-project baseline and
# no knowledge of the repo's stack. Per-project "referenced asset/link resolves"
# checks live in each repo's own .githooks/pre-commit and are chained by the
# managed pre-commit.
#
# Tuning / override (env):
#   HERMES_MASS_DELETION_LIMIT=<n>   threshold (default 10)
#   HERMES_ALLOW_MASS_DELETION=1     bypass for a legitimate large removal
#
# Exit: 0 = ok (or overridden), non-zero = blocked.
set -euo pipefail

LIMIT="${HERMES_MASS_DELETION_LIMIT:-10}"

# Staged deletions only (what this commit would actually record).
deleted="$(git diff --cached --diff-filter=D --name-only || true)"

count=0
[ -n "$deleted" ] && count="$(printf '%s\n' "$deleted" | sed '/^$/d' | wc -l | tr -d ' ')"

if [ "$count" -gt "$LIMIT" ]; then
  printf '✗ mass-deletion guard: this commit deletes %s tracked file(s) (limit %s):\n' "$count" "$LIMIT" >&2
  printf '%s\n' "$deleted" | sed '/^$/d' | sed 's/^/    - /' >&2
  if [ "${HERMES_ALLOW_MASS_DELETION:-0}" = "1" ]; then
    echo "  HERMES_ALLOW_MASS_DELETION=1 set — allowing this deletion." >&2
    exit 0
  fi
  echo "  Refusing to commit. A blind 'git add -A' over a working tree that is" >&2
  echo "  missing files (rebase/build state) looks exactly like this." >&2
  echo "  If the deletion is intentional, re-run with:" >&2
  echo "      HERMES_ALLOW_MASS_DELETION=1 git commit ..." >&2
  echo "  and explain the removal in the commit body." >&2
  exit 1
fi

echo "✓ mass-deletion guard: $count tracked deletion(s) (limit $LIMIT)."
exit 0
