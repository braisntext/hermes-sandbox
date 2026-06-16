"""Regression lock for safety net #5 — per-profile git/GitHub auth.

Guards the failure modes that actually happened to the finview / grow-shop
delegate lanes (PRs #31/#32/#35) and, especially, the agent's own "fixes" that
blank the working credential or detour to SSH:

  * BLANKED / MISSING / EMPTY ``.git-credentials`` (token rewritten ``***``).
  * credentials not in the ``https://x-access-token:<token>@github.com`` form.
  * a workspace repo remote using SSH or embedding a token.
  * ``credential.helper`` not set to ``store``.

Fully hermetic: builds synthetic profile-home fixtures under ``tmp_path`` (fake
``.git-credentials`` / ``.gitconfig`` / ``.git/config`` files — no real repos,
no subprocess, no network). All tokens are obviously fake (FAKE / NOTREAL).
"""
from pathlib import Path

import pytest

from evals.checks.profile_git_auth import (
    audit_profile_git_auth,
    audit_summary,
    git_auth_hazard,
    remote_hazard,
)
from evals.run import evaluate, load_case

# Shape-only fake — never a real credential.
FAKE_TOKEN = "ghp_FAKE0000example0000token0000NOTREAL"

_REMOTE_URLS = {
    "tokenless": "https://github.com/acme/repo.git",
    "ssh": "git@github.com:acme/repo.git",
    "ssh_proto": "ssh://git@github.com/acme/repo.git",
    "token": f"https://x-access-token:{FAKE_TOKEN}@github.com/acme/repo.git",
}


def _make_home(
    base: Path,
    name: str,
    *,
    creds: str = "valid",
    helper: str | None = "store",
    remote: str | None = "tokenless",
) -> Path:
    """Build a synthetic ``<profile>/home`` fixture and return its path.

    ``creds``: valid | blanked | empty | missing | wrong_form
    ``helper``: gitconfig credential.helper value, or None to omit it
    ``remote``: a key of _REMOTE_URLS for the workspace repo, or None for no repo
    """
    prof = base / "profiles" / name
    home = prof / "home"
    home.mkdir(parents=True)

    cred_file = home / ".git-credentials"
    if creds == "valid":
        cred_file.write_text(f"https://x-access-token:{FAKE_TOKEN}@github.com\n")
    elif creds == "blanked":
        cred_file.write_text("https://x-access-token:***@github.com\n")
    elif creds == "empty":
        cred_file.write_text("")
    elif creds == "wrong_form":
        cred_file.write_text(f"https://user:{FAKE_TOKEN}@github.com\n")
    elif creds == "missing":
        pass
    else:  # pragma: no cover - guard against typos in tests
        raise ValueError(f"unknown creds fixture: {creds}")

    if helper is not None:
        (home / ".gitconfig").write_text(f"[credential]\n\thelper = {helper}\n")

    if remote is not None:
        repo = prof / "workspace" / "repo"
        (repo / ".git").mkdir(parents=True)
        (repo / ".git" / "config").write_text(
            f'[remote "origin"]\n\turl = {_REMOTE_URLS[remote]}\n'
        )

    return home


class TestGoodProfileIsClean:
    def test_valid_profile_has_no_hazard(self, tmp_path):
        home = _make_home(tmp_path, "finview")
        assert git_auth_hazard(home) is None

    def test_valid_profile_without_workspace_is_clean(self, tmp_path):
        home = _make_home(tmp_path, "finview", remote=None)
        assert git_auth_hazard(home) is None


class TestCredentialHazards:
    def test_missing_credentials_is_flagged(self, tmp_path):
        home = _make_home(tmp_path, "finview", creds="missing")
        hz = git_auth_hazard(home)
        assert hz and "missing" in hz

    def test_empty_credentials_is_flagged(self, tmp_path):
        home = _make_home(tmp_path, "finview", creds="empty")
        hz = git_auth_hazard(home)
        assert hz and "empty" in hz

    def test_blanked_redacted_credentials_is_flagged(self, tmp_path):
        home = _make_home(tmp_path, "finview", creds="blanked")
        hz = git_auth_hazard(home)
        assert hz and "redacted" in hz

    def test_wrong_form_credentials_is_flagged(self, tmp_path):
        home = _make_home(tmp_path, "finview", creds="wrong_form")
        hz = git_auth_hazard(home)
        assert hz and "wrong form" in hz

    def test_blanked_credential_never_echoes_a_token(self, tmp_path):
        # The whole point: the redacted token must not leak through the hazard.
        home = _make_home(tmp_path, "finview", creds="blanked")
        hz = git_auth_hazard(home)
        assert "***@github.com" not in (hz or "")


class TestHelperHazards:
    def test_missing_helper_is_flagged(self, tmp_path):
        home = _make_home(tmp_path, "finview", helper=None)
        hz = git_auth_hazard(home)
        assert hz and "credential.helper" in hz

    def test_wrong_helper_is_flagged(self, tmp_path):
        home = _make_home(tmp_path, "finview", helper="cache")
        hz = git_auth_hazard(home)
        assert hz and "credential.helper" in hz


class TestRemoteHazards:
    def test_ssh_remote_is_flagged(self, tmp_path):
        home = _make_home(tmp_path, "grow-shop", remote="ssh")
        hz = git_auth_hazard(home)
        assert hz and "SSH" in hz

    def test_ssh_protocol_remote_is_flagged(self, tmp_path):
        home = _make_home(tmp_path, "grow-shop", remote="ssh_proto")
        hz = git_auth_hazard(home)
        assert hz and "SSH" in hz

    def test_token_in_remote_url_is_flagged(self, tmp_path):
        home = _make_home(tmp_path, "grow-shop", remote="token")
        hz = git_auth_hazard(home)
        assert hz and "embeds a token" in hz

    def test_token_in_remote_url_is_redacted(self, tmp_path):
        home = _make_home(tmp_path, "grow-shop", remote="token")
        hz = git_auth_hazard(home)
        assert FAKE_TOKEN not in (hz or "")

    def test_tokenless_remote_is_clean(self, tmp_path):
        repo = tmp_path / "repo"
        (repo / ".git").mkdir(parents=True)
        (repo / ".git" / "config").write_text(
            '[remote "origin"]\n\turl = https://github.com/acme/repo.git\n'
        )
        assert remote_hazard(repo) is None

    def test_no_origin_remote_is_not_a_hazard(self, tmp_path):
        repo = tmp_path / "repo"
        (repo / ".git").mkdir(parents=True)
        (repo / ".git" / "config").write_text("[core]\n\tbare = false\n")
        assert remote_hazard(repo) is None


class TestAuditAggregation:
    def test_audit_collects_only_hazardous_profiles(self, tmp_path):
        good = _make_home(tmp_path, "finview")
        bad = _make_home(tmp_path, "grow-shop", creds="blanked")
        flagged = audit_profile_git_auth([good, bad])
        names = [name for name, _ in flagged]
        assert names == ["grow-shop"]

    def test_summary_ok_when_all_clean(self, tmp_path):
        good = _make_home(tmp_path, "finview")
        assert audit_summary([good]).startswith("OK")

    def test_summary_fail_names_the_profile(self, tmp_path):
        bad = _make_home(tmp_path, "grow-shop", remote="ssh")
        summary = audit_summary([bad])
        assert summary.startswith("FAIL")
        assert "grow-shop" in summary


class TestEvalHarnessAgreement:
    def test_seed_case_runs_against_live_homes(self):
        # Live profile homes may be absent in CI; the case must evaluate cleanly.
        case = load_case("profile_git_auth")
        output, results = evaluate(case, use_llm=False)
        assert "OK" in output or "FAIL" in output
        assert results
