"""Subprocess entry point for profile-scoped delegated tasks.

Spawned by :func:`hermes_cli.delegate_core.run_delegate_in_profile` as::

    HERMES_HOME=/opt/data/profiles/<profile> \\
        python -m hermes_cli.delegate_runner <task_id>

The prompt is read from stdin. The JSON result is written to stdout framed by
the sentinels in :mod:`hermes_cli.delegate_core` so the parent can recover it
even if the agent emits stray output. ``HERMES_HOME`` is honored because the
agent reads its config / session DB / memory from that environment variable.
"""

from __future__ import annotations

import json
import os
import sys


def main() -> int:
    from hermes_cli.delegate_core import (
        RESULT_PREFIX,
        RESULT_SUFFIX,
        run_delegate_agent,
    )

    task_id = sys.argv[1] if len(sys.argv) > 1 else ""
    prompt = sys.stdin.read()

    # When spawned by the Telegram gateway path (no_delegate_prompt=True on the
    # parent), skip the BigLobster-specific ephemeral prompt so the profile's
    # own soul drives the session instead.
    no_prompt = bool(os.environ.get("HERMES_DELEGATE_NO_PROMPT", ""))
    system_prompt_kwarg = {"ephemeral_system_prompt": None} if no_prompt else {}

    try:
        result = run_delegate_agent(task_id, prompt, **system_prompt_kwarg)
        out = {
            "final_response": result.get("final_response", ""),
            "error": result.get("error"),
        }
    except Exception as exc:  # noqa: BLE001 - report any failure back to the parent
        out = {"final_response": "", "error": str(exc)}

    sys.stdout.write(RESULT_PREFIX + json.dumps(out) + RESULT_SUFFIX + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
