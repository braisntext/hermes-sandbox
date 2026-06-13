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
    last_init_kwargs: dict = {}

    def __init__(self, **kwargs):
        _FakeAgent.last_init_kwargs = kwargs
        # Default: no fallback used this turn (notice helper returns "").
        self._fallback_index = 0
        self._primary_runtime = {"model": kwargs.get("model")}
        self.model = kwargs.get("model")

    def run_conversation(self, **kwargs):
        _FakeAgent.last_kwargs = kwargs
        return {"final_response": "ok", "error": None}


def _patch_agent_runtime(session_db, config=None):
    """Patch the heavy deps of run_delegate_agent so it runs as a pure unit."""
    return [
        patch("hermes_cli.config.load_config",
              return_value=config if config is not None else {"model": "x/y"}),
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


def test_bounded_resume_history_small_input_untouched():
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    assert delegate_core._bounded_resume_history(msgs, budget=1000) == msgs


def test_bounded_resume_history_zero_budget_disables_bounding():
    msgs = [{"role": "user", "content": "x" * 10_000}]
    assert delegate_core._bounded_resume_history(msgs, budget=0) == msgs


def test_bounded_resume_history_trims_to_budget_and_user_boundary():
    # Old, large turns that must be dropped; a recent pair that fits the budget.
    msgs = [
        {"role": "user", "content": "OLD" * 1000},        # ~3000 chars, dropped
        {"role": "assistant", "content": "OLD" * 1000},   # dropped
        {"role": "user", "content": "recent question"},
        {"role": "assistant", "content": "recent answer"},
    ]
    out = delegate_core._bounded_resume_history(msgs, budget=200)
    # Only the recent pair survives, and the window opens on a user turn.
    assert out == [
        {"role": "user", "content": "recent question"},
        {"role": "assistant", "content": "recent answer"},
    ]
    assert out[0]["role"] == "user"


def test_bounded_resume_history_front_trim_drops_orphan_tool_result():
    # A budget that would otherwise open the window on a tool result (no matching
    # assistant tool_calls in-window) must be trimmed to the next user turn.
    msgs = [
        {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
        {"role": "tool", "tool_call_id": "1", "content": "result"},
        {"role": "user", "content": "next"},
        {"role": "assistant", "content": "ok"},
    ]
    out = delegate_core._bounded_resume_history(msgs, budget=40)
    # Window must not start on the orphan tool row.
    assert out[0]["role"] in {"user", "assistant"}
    assert all(
        not (i == 0 and m.get("role") == "tool") for i, m in enumerate(out)
    )


def test_delegate_passes_fallback_model_from_config():
    """A configured fallback_model must be forwarded to the agent in the delegate lane."""
    fb = {"provider": "openrouter", "model": "tencent/hy3-preview"}
    cfg = {"model": "openrouter/owl-alpha", "fallback_model": fb}
    patches = _patch_agent_runtime(_FakeSessionDB(history=False), config=cfg)
    for p in patches:
        p.start()
    try:
        delegate_core.run_delegate_agent("t", "hi")
    finally:
        for p in patches:
            p.stop()
    assert _FakeAgent.last_init_kwargs.get("fallback_model") == fb


def test_delegate_omits_fallback_model_when_absent():
    """Without fallback_model in config the agent kwarg must not be set."""
    patches = _patch_agent_runtime(_FakeSessionDB(history=False), config={"model": "m"})
    for p in patches:
        p.start()
    try:
        delegate_core.run_delegate_agent("t", "hi")
    finally:
        for p in patches:
            p.stop()
    assert "fallback_model" not in _FakeAgent.last_init_kwargs


def test_fallback_switch_notice():
    from agent.chat_completion_helpers import fallback_switch_notice
    from types import SimpleNamespace

    # No fallback used → empty notice.
    assert fallback_switch_notice(
        SimpleNamespace(_fallback_index=0, _primary_runtime={"model": "p"}, model="p")
    ) == ""

    # Fallback used → names primary and current model.
    notice = fallback_switch_notice(
        SimpleNamespace(
            _fallback_index=1,
            _primary_runtime={"model": "openrouter/owl-alpha"},
            model="tencent/hy3-preview",
        )
    )
    assert "owl-alpha" in notice and "tencent/hy3-preview" in notice
    assert notice.startswith("ℹ️")

    # Index advanced but model unchanged (deduped chain) → no false notice.
    assert fallback_switch_notice(
        SimpleNamespace(_fallback_index=1, _primary_runtime={"model": "m"}, model="m")
    ) == ""


def test_delegate_appends_fallback_notice_to_reply():
    """When the agent ends on a fallback model, the delegate reply carries the notice."""
    class _FallbackAgent(_FakeAgent):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self._fallback_index = 1
            self._primary_runtime = {"model": "openrouter/owl-alpha"}
            self.model = "tencent/hy3-preview"

        def run_conversation(self, **kwargs):
            return {"final_response": "respuesta", "error": None}

    patches = [
        patch("hermes_cli.config.load_config",
              return_value={"model": "openrouter/owl-alpha"}),
        patch("hermes_state.SessionDB", return_value=_FakeSessionDB(history=False)),
        patch("hermes_cli.runtime_provider.resolve_runtime_provider",
              side_effect=Exception("skip")),
        patch("run_agent.AIAgent", _FallbackAgent),
    ]
    for p in patches:
        p.start()
    try:
        result = delegate_core.run_delegate_agent("t", "hola")
    finally:
        for p in patches:
            p.stop()
    assert result["final_response"].startswith("respuesta")
    assert "tencent/hy3-preview" in result["final_response"]
