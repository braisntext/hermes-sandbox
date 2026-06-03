"""Agent runner for delegated tasks from an external orchestrator
(BigLobster COO -> ``/api/delegate``).

Lives outside ``web_server.py`` so the same runner works in two execution modes:

* **in-process** (default profile) - called from a thread-pool executor;
* **in a subprocess** under a per-customer profile's ``HERMES_HOME`` - see
  :func:`run_delegate_in_profile` and ``hermes_cli.delegate_runner``.

A subprocess is required for the profile-scoped path because the web-server
process is pinned to the default profile's ``HERMES_HOME``; setting it per-task
in-process would race across concurrent delegations. The child inherits the
parent environment with ``HERMES_HOME`` overridden, so its session DB and memory
land in the target profile.

Importing this module must stay cheap and side-effect free (it must NOT boot the
FastAPI web server), so the heavy agent imports are deferred into the functions.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import uuid
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Sentinels framing the JSON result emitted by the subprocess runner on stdout.
# Lets the parent recover the result even if the agent prints stray output.
RESULT_PREFIX = "<<<HERMES_DELEGATE_RESULT>>>"
RESULT_SUFFIX = "<<<END_HERMES_DELEGATE_RESULT>>>"

# Default wall-clock timeout (seconds) for a profile-scoped delegate subprocess.
_DEFAULT_DELEGATE_TIMEOUT = int(os.environ.get("HERMES_DELEGATE_TIMEOUT", "1800"))


DELEGATE_SYSTEM_PROMPT = (
    "You are executing a delegated task from an external orchestrator (BigLobster). "
    "Complete it fully and autonomously — no clarifying questions.\n\n"
    "TOOL USE: Always use your tools — never produce a code artifact as a substitute.\n"
    "- Image generation → call `image_generate`. Do NOT write HTML/CSS/JS to simulate an image.\n"
    "- The generated image path is returned in the tool result. Reference it in your response.\n\n"
    "FILE OUTPUT: The only writable volume is /opt/data/. Write output files there.\n"
    "- Use /opt/data/biglobster/ for BigLobster-related output.\n"
    "- Create subdirectories as needed with shell commands or write_file.\n"
    "- Do NOT write to /workspace/, /tmp/, or any path outside /opt/data/."
)


def run_delegate_agent(task_id: str, prompt: str) -> dict:
    """Synchronous agent runner.

    Reads config from the active ``HERMES_HOME``; running this under a profile's
    ``HERMES_HOME`` therefore scopes the session DB and memory to that profile.
    """
    from run_agent import AIAgent
    from hermes_cli.config import load_config
    from hermes_cli.runtime_provider import resolve_runtime_provider
    from hermes_state import SessionDB

    config = load_config()
    model_cfg = config.get("model")
    default_model = ""
    config_provider = None
    if isinstance(model_cfg, dict):
        default_model = str(model_cfg.get("default") or "")
        config_provider = model_cfg.get("provider")
    elif isinstance(model_cfg, str) and model_cfg.strip():
        default_model = model_cfg.strip()

    try:
        session_db = SessionDB()
    except Exception:
        logger.warning("Delegate: SessionDB unavailable, session will not be persisted")
        session_db = None

    kwargs: Dict[str, Any] = {
        "platform": "api",
        "quiet_mode": True,
        "session_id": task_id or str(uuid.uuid4()),
        "model": default_model,
        "session_db": session_db,
        "ephemeral_system_prompt": DELEGATE_SYSTEM_PROMPT,
    }
    try:
        runtime = resolve_runtime_provider(requested=config_provider)
        kwargs.update(
            {
                "provider": runtime.get("provider"),
                "api_mode": runtime.get("api_mode"),
                "base_url": runtime.get("base_url"),
                "api_key": runtime.get("api_key"),
                "command": runtime.get("command"),
                "args": list(runtime.get("args") or []),
            }
        )
    except Exception:
        logger.debug("Delegate falling back to default provider resolution", exc_info=True)

    agent = AIAgent(**kwargs)
    return agent.run_conversation(user_message=prompt, task_id=task_id)


def run_delegate_in_profile(task_id: str, prompt: str, profile: str) -> dict:
    """Run a delegated task inside *profile*'s ``HERMES_HOME``, in a subprocess.

    Returns the same ``{"final_response", "error"}`` shape as the agent so the
    caller's callback path is unchanged. Errors (unknown profile, timeout,
    subprocess failure) are returned as ``error`` rather than raised.
    """
    from hermes_cli import profiles as profiles_mod

    try:
        profile_home = profiles_mod.resolve_profile_env(profile)
    except (FileNotFoundError, ValueError) as exc:
        return {"final_response": "", "error": f"Invalid delegate profile {profile!r}: {exc}"}

    env = {**os.environ, "HERMES_HOME": profile_home}

    # Scope the subprocess's working directory to the profile's own workspace.
    # Without this the subprocess inherits the web-server process's cwd (the
    # default HERMES_HOME root, e.g. /opt/data), so a customer task would
    # operate in — and could read/write — the default profile's files and
    # other tenants' data. Anchoring cwd to <profile_home>/workspace keeps a
    # delegated task's file operations inside its own profile. Created on
    # demand so a freshly-onboarded profile still has a valid cwd; falls back
    # to the profile root if the workspace dir can't be created.
    workdir = os.path.join(profile_home, "workspace")
    try:
        os.makedirs(workdir, exist_ok=True)
    except OSError:
        workdir = profile_home

    cmd = [sys.executable, "-m", "hermes_cli.delegate_runner", task_id]
    try:
        proc = subprocess.run(
            cmd,
            input=prompt,
            env=env,
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=_DEFAULT_DELEGATE_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return {
            "final_response": "",
            "error": f"Delegate task timed out after {_DEFAULT_DELEGATE_TIMEOUT}s (profile {profile!r})",
        }
    except Exception as exc:  # noqa: BLE001 - surface any spawn failure to the orchestrator
        return {"final_response": "", "error": f"Delegate subprocess failed: {exc}"}

    result = _parse_runner_output(proc.stdout)
    if result is None:
        tail = (proc.stderr or proc.stdout or "")[-500:]
        return {
            "final_response": "",
            "error": (
                f"Delegate subprocess produced no parseable result "
                f"(exit={proc.returncode}, profile {profile!r}). Tail: {tail}"
            ),
        }
    return result


def _parse_runner_output(stdout: str) -> Optional[dict]:
    """Extract the JSON result framed by the runner sentinels from *stdout*."""
    if not stdout:
        return None
    start = stdout.rfind(RESULT_PREFIX)
    end = stdout.rfind(RESULT_SUFFIX)
    if start == -1 or end == -1 or end <= start:
        return None
    blob = stdout[start + len(RESULT_PREFIX):end]
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        return None
    return {
        "final_response": data.get("final_response", ""),
        "error": data.get("error"),
    }
