"""Profile-scoped delegate: the subprocess must run in the profile's own
workspace (not the shared default HERMES_HOME root), and unknown profiles must
return an error rather than raise. Covers run_delegate_in_profile."""
from __future__ import annotations

import os
from unittest.mock import patch

from hermes_cli import delegate_core


class _FakeCompleted:
    def __init__(self, stdout: str):
        self.stdout = stdout
        self.stderr = ""
        self.returncode = 0


def _ok_stdout() -> str:
    return (
        delegate_core.RESULT_PREFIX
        + '{"final_response": "ok", "error": null}'
        + delegate_core.RESULT_SUFFIX
    )


def test_subprocess_cwd_scoped_to_profile_workspace(tmp_path):
    profile_home = tmp_path / "profiles" / "grow-shop"
    profile_home.mkdir(parents=True)

    captured: dict = {}

    def _fake_run(cmd, **kwargs):
        captured.update(kwargs)
        return _FakeCompleted(_ok_stdout())

    with patch("hermes_cli.profiles.resolve_profile_env", return_value=str(profile_home)), \
            patch("subprocess.run", _fake_run):
        result = delegate_core.run_delegate_in_profile("task-1", "do it", "grow-shop")

    assert result["final_response"] == "ok"
    # cwd is the profile's OWN workspace, created on demand — not /opt/data.
    expected_workdir = str(profile_home / "workspace")
    assert captured["cwd"] == expected_workdir
    assert os.path.isdir(expected_workdir)
    # HERMES_HOME still points at the profile home (memory/session isolation).
    assert captured["env"]["HERMES_HOME"] == str(profile_home)


def test_cwd_falls_back_to_profile_root_if_workspace_uncreatable(tmp_path):
    profile_home = tmp_path / "profiles" / "grow-shop"
    profile_home.mkdir(parents=True)

    captured: dict = {}

    def _fake_run(cmd, **kwargs):
        captured.update(kwargs)
        return _FakeCompleted(_ok_stdout())

    with patch("hermes_cli.profiles.resolve_profile_env", return_value=str(profile_home)), \
            patch("os.makedirs", side_effect=OSError("read-only fs")), \
            patch("subprocess.run", _fake_run):
        delegate_core.run_delegate_in_profile("task-1", "do it", "grow-shop")

    assert captured["cwd"] == str(profile_home)


def test_unknown_profile_returns_error_not_raise():
    with patch("hermes_cli.profiles.resolve_profile_env",
               side_effect=FileNotFoundError("nope")):
        result = delegate_core.run_delegate_in_profile("task-1", "p", "ghost")
    assert result["final_response"] == ""
    assert "Invalid delegate profile" in result["error"]


def test_no_delegate_prompt_sets_env_var(tmp_path):
    """no_delegate_prompt=True must inject HERMES_DELEGATE_NO_PROMPT=1 into the subprocess env."""
    profile_home = tmp_path / "profiles" / "grow-shop"
    profile_home.mkdir(parents=True)

    captured: dict = {}

    def _fake_run(cmd, **kwargs):
        captured.update(kwargs)
        return _FakeCompleted(_ok_stdout())

    with patch("hermes_cli.profiles.resolve_profile_env", return_value=str(profile_home)), \
            patch("subprocess.run", _fake_run):
        delegate_core.run_delegate_in_profile(
            "task-1", "do it", "grow-shop", no_delegate_prompt=True
        )

    assert captured["env"].get("HERMES_DELEGATE_NO_PROMPT") == "1"


def test_delegate_prompt_absent_by_default(tmp_path):
    """Without no_delegate_prompt the env var must NOT be set."""
    profile_home = tmp_path / "profiles" / "grow-shop"
    profile_home.mkdir(parents=True)

    captured: dict = {}

    def _fake_run(cmd, **kwargs):
        captured.update(kwargs)
        return _FakeCompleted(_ok_stdout())

    with patch("hermes_cli.profiles.resolve_profile_env", return_value=str(profile_home)), \
            patch("subprocess.run", _fake_run):
        delegate_core.run_delegate_in_profile("task-1", "do it", "grow-shop")

    assert "HERMES_DELEGATE_NO_PROMPT" not in captured["env"]


def test_auto_profile_field_on_message_event():
    """MessageEvent must carry auto_profile=None by default and accept a profile name."""
    from gateway.platforms.base import MessageEvent

    ev = MessageEvent(text="hello")
    assert ev.auto_profile is None

    ev2 = MessageEvent(text="hello", auto_profile="grow-shop")
    assert ev2.auto_profile == "grow-shop"


def test_resume_history_sets_env_var(tmp_path):
    """resume_history=True must inject HERMES_DELEGATE_RESUME=1 into the subprocess env."""
    profile_home = tmp_path / "profiles" / "finview"
    profile_home.mkdir(parents=True)

    captured: dict = {}

    def _fake_run(cmd, **kwargs):
        captured.update(kwargs)
        return _FakeCompleted(_ok_stdout())

    with patch("hermes_cli.profiles.resolve_profile_env", return_value=str(profile_home)), \
            patch("subprocess.run", _fake_run):
        delegate_core.run_delegate_in_profile(
            "task-1", "do it", "finview", resume_history=True
        )

    assert captured["env"].get("HERMES_DELEGATE_RESUME") == "1"


def test_resume_history_absent_by_default(tmp_path):
    """Without resume_history the env var must NOT be set (one-shot delegations stay stateless)."""
    profile_home = tmp_path / "profiles" / "finview"
    profile_home.mkdir(parents=True)

    captured: dict = {}

    def _fake_run(cmd, **kwargs):
        captured.update(kwargs)
        return _FakeCompleted(_ok_stdout())

    with patch("hermes_cli.profiles.resolve_profile_env", return_value=str(profile_home)), \
            patch("subprocess.run", _fake_run):
        delegate_core.run_delegate_in_profile("task-1", "do it", "finview")

    assert "HERMES_DELEGATE_RESUME" not in captured["env"]


class _FakeSessionDB:
    def __init__(self, history):
        self._history = history

    def get_messages_as_conversation(self, session_id):
        # Echo the session_id so the test can assert we loaded by the right key.
        return [{"role": "assistant", "content": f"prior for {session_id}"}] \
            if self._history else []


class _FakeAgent:
    last_kwargs: dict = {}

    def __init__(self, **kwargs):
        pass

    def run_conversation(self, **kwargs):
        _FakeAgent.last_kwargs = kwargs
        return {"final_response": "ok", "error": None}


def _patch_agent_runtime(session_db):
    """Patch the heavy deps of run_delegate_agent so it runs as a pure unit."""
    return [
        patch("hermes_cli.config.load_config", return_value={"model": "x/y"}),
        patch("hermes_state.SessionDB", return_value=session_db),
        patch("hermes_cli.runtime_provider.resolve_runtime_provider",
              side_effect=Exception("skip runtime resolution")),
        patch("run_agent.AIAgent", _FakeAgent),
    ]


def test_run_delegate_agent_rehydrates_when_resume():
    """resume_history=True loads the per-task transcript and passes it as conversation_history."""
    fake_db = _FakeSessionDB(history=True)
    patches = _patch_agent_runtime(fake_db)
    for p in patches:
        p.start()
    try:
        delegate_core.run_delegate_agent(
            "agent:main:telegram:group:-100:61", "sí, hazlo", resume_history=True
        )
    finally:
        for p in patches:
            p.stop()

    history = _FakeAgent.last_kwargs.get("conversation_history")
    assert history == [
        {"role": "assistant", "content": "prior for agent:main:telegram:group:-100:61"}
    ]


def test_run_delegate_agent_stateless_without_resume():
    """Without resume_history the agent starts with no conversation_history (one-shot lane)."""
    fake_db = _FakeSessionDB(history=True)
    patches = _patch_agent_runtime(fake_db)
    for p in patches:
        p.start()
    try:
        delegate_core.run_delegate_agent("one-shot-task", "do it")
    finally:
        for p in patches:
            p.stop()

    assert _FakeAgent.last_kwargs.get("conversation_history") is None
