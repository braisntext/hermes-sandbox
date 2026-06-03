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
