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

# Character budget for the resume-history window (~4 chars/token, so the default
# is roughly 20K tokens). A long-lived topic accumulates a huge transcript — a
# finview topic reached 669 messages / ~277K tokens — and replaying all of it on
# every turn sends a ~440K-token request that is slow and burns the (often
# rate-limited) model's capacity, starving other threads. A recent window is
# enough to carry the thread's focus and the immediately-prior proposed task
# across turns. Set to 0 to disable bounding (replay everything). Overridable via
# HERMES_DELEGATE_RESUME_CHARS so prod can tune without a redeploy.
_RESUME_HISTORY_CHAR_BUDGET = int(
    os.environ.get("HERMES_DELEGATE_RESUME_CHARS", "80000")
)


def _bounded_resume_history(
    messages: Optional[list], budget: int = _RESUME_HISTORY_CHAR_BUDGET
) -> Optional[list]:
    """Return a recent, replay-safe tail of *messages* within *budget* chars.

    Keeps the most recent messages up to the budget, then trims the front to a
    clean turn boundary so the slice never starts on an orphan ``tool`` result
    or a dangling assistant ``tool_calls`` (both of which the API rejects). A
    ``budget`` of 0 (or empty input) returns the messages unchanged.
    """
    if not messages or budget <= 0:
        return messages

    kept: list = []
    total = 0
    for msg in reversed(messages):
        size = len(str(msg.get("content") or ""))
        tool_calls = msg.get("tool_calls")
        if tool_calls:
            size += len(str(tool_calls))
        # Always keep at least the most recent message, even if it alone exceeds
        # the budget, so the current turn still has its immediate predecessor.
        if kept and total + size > budget:
            break
        kept.append(msg)
        total += size
    kept.reverse()

    # Front must be a safe opening message. Prefer the first user turn; fall
    # back to the first assistant message that has no pending tool_calls.
    for i, msg in enumerate(kept):
        if msg.get("role") == "user":
            return kept[i:]
    for i, msg in enumerate(kept):
        if msg.get("role") == "assistant" and not msg.get("tool_calls"):
            return kept[i:]
    return kept


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


def run_delegate_agent(
    task_id: str,
    prompt: str,
    ephemeral_system_prompt: Optional[str] = DELEGATE_SYSTEM_PROMPT,
    resume_history: bool = False,
) -> dict:
    """Synchronous agent runner.

    Reads config from the active ``HERMES_HOME``; running this under a profile's
    ``HERMES_HOME`` therefore scopes the session DB and memory to that profile.

    Pass ``ephemeral_system_prompt=None`` to let the profile's own soul take over
    (used by the Telegram gateway path, which needs the native profile persona).

    Pass ``resume_history=True`` to rehydrate the prior conversation for
    ``task_id`` from the profile's session DB before running. Each delegate call
    spawns a fresh agent, so without this the conversation starts empty every
    turn and the agent cannot see its own prior turns — fine for one-shot
    orchestrator tasks (unique ``task_id`` each call), but it makes a *standing*
    conversational lane (Telegram forum topic bound to a profile) amnesiac
    turn-to-turn. The transcript is already persisted under ``task_id`` (=the
    gateway session key, per chat+thread); this just reads it back, bounded to a
    recent window (see ``_bounded_resume_history``) so a huge backlog doesn't
    inflate every request.
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
        "ephemeral_system_prompt": ephemeral_system_prompt,
    }
    # Wire the configured fallback model so a profile-routed topic fails over
    # when its primary model is rate-limited (the in-process gateway lane already
    # loads this from config; the delegate subprocess must do it explicitly).
    fallback_model = config.get("fallback_model")
    if fallback_model:
        kwargs["fallback_model"] = fallback_model
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

    # Rehydrate the prior conversation for this task_id so a standing
    # conversational lane (e.g. a profile-bound Telegram topic) remembers
    # earlier turns. Loaded by the exact session_id, so no cross-thread or
    # cross-profile bleed. Empty/missing history is a no-op (genuine first turn).
    conversation_history = None
    if resume_history and session_db is not None and task_id:
        try:
            full_history = session_db.get_messages_as_conversation(task_id)
            conversation_history = _bounded_resume_history(full_history)
            if full_history and conversation_history is not None:
                logger.info(
                    "Delegate: resumed %d/%d prior messages for task_id=%s "
                    "(char budget=%d)",
                    len(conversation_history), len(full_history), task_id,
                    _RESUME_HISTORY_CHAR_BUDGET,
                )
        except Exception:
            logger.warning(
                "Delegate: failed to rehydrate history for task_id=%s; "
                "starting fresh", task_id, exc_info=True,
            )
            conversation_history = None

    agent = AIAgent(**kwargs)
    result = agent.run_conversation(
        user_message=prompt,
        conversation_history=conversation_history,
        task_id=task_id,
    )

    # If the turn fell back to the configured backup model (primary rate-limited),
    # tell the user — the delegate subprocess has no live status channel, so the
    # notice rides along on the reply itself.
    try:
        from agent.chat_completion_helpers import fallback_switch_notice
        notice = fallback_switch_notice(agent)
        if notice:
            body = str(result.get("final_response") or "").rstrip()
            result["final_response"] = f"{body}\n\n{notice}" if body else notice
    except Exception:
        logger.debug("Delegate: fallback notice append failed", exc_info=True)

    return result


def _apply_profile_git_auth(env: Dict[str, str], profile_home: str) -> None:
    """Make the delegate subprocess able to ``git push`` / ``gh pr create`` with
    ambient credentials, identical to the default/cron lane.

    The interactive profile-delegate lane previously inherited only the gateway
    process's ``HOME`` (the *default* profile's), so git/gh in a tenant topic
    used the wrong credential store — or none — and the agent ended up asking
    the user to paste a raw token. Two fixes, both in-place on *env*:

    * **HOME pin.** When ``<profile_home>/home`` exists (the per-profile
      subprocess HOME that the container boot hook populates with
      ``.git-credentials`` + ``.gitconfig`` — see ``get_subprocess_home`` and
      ``docker/cont-init.d/03-biglobster-config`` §4), point the subprocess at
      it. This makes the delegate *process itself* — not just the grandchild
      git/gh processes that re-derive HOME via ``get_subprocess_home`` — use the
      tenant's credentialed HOME. Guarded on existence so we never strand the
      lane on an empty HOME: if the dir is absent we leave HOME untouched and
      the existing fallback (the gateway's credentialed HOME) stands.

    * **Token mirror.** ``gh`` accepts ``GH_TOKEN`` *or* ``GITHUB_TOKEN``, but
      the subprocess env blocklist strips ``GH_TOKEN`` while letting
      ``GITHUB_TOKEN`` through (see ``_HERMES_PROVIDER_ENV_BLOCKLIST``). Mirror
      whichever is present onto the other so the surviving var (``GITHUB_TOKEN``)
      is always populated for ``gh``, and in-process consumers that read
      ``GH_TOKEN`` still work.
    """
    profile_subprocess_home = os.path.join(profile_home, "home")
    if os.path.isdir(profile_subprocess_home):
        env["HOME"] = profile_subprocess_home

    github_token = env.get("GITHUB_TOKEN") or env.get("GH_TOKEN")
    if github_token:
        env.setdefault("GITHUB_TOKEN", github_token)
        env.setdefault("GH_TOKEN", github_token)


def run_delegate_in_profile(
    task_id: str,
    prompt: str,
    profile: str,
    *,
    no_delegate_prompt: bool = False,
    resume_history: bool = False,
) -> dict:
    """Run a delegated task inside *profile*'s ``HERMES_HOME``, in a subprocess.

    Returns the same ``{"final_response", "error"}`` shape as the agent so the
    caller's callback path is unchanged. Errors (unknown profile, timeout,
    subprocess failure) are returned as ``error`` rather than raised.

    Pass ``no_delegate_prompt=True`` for the Telegram gateway path: the subprocess
    will skip the ``DELEGATE_SYSTEM_PROMPT`` and use the profile's own soul instead.

    Pass ``resume_history=True`` to make the subprocess rehydrate the prior
    conversation for ``task_id`` (per chat+thread) before running — used by the
    Telegram topic lane so a profile-bound thread is stateful across turns. Left
    off for one-shot orchestrator delegations, which must stay stateless.
    """
    from hermes_cli import profiles as profiles_mod

    try:
        profile_home = profiles_mod.resolve_profile_env(profile)
    except (FileNotFoundError, ValueError) as exc:
        return {"final_response": "", "error": f"Invalid delegate profile {profile!r}: {exc}"}

    env = {**os.environ, "HERMES_HOME": profile_home}
    _apply_profile_git_auth(env, profile_home)
    if no_delegate_prompt:
        env["HERMES_DELEGATE_NO_PROMPT"] = "1"
    if resume_history:
        env["HERMES_DELEGATE_RESUME"] = "1"

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
