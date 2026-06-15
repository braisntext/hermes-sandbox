"""Contract test: docker/cont-init.d/03-biglobster-config refreshes the git
HTTPS credential for EVERY profile's subprocess HOME on every boot, not just
the default profile.

Per-profile cron jobs run git with HOME=$HERMES_HOME/profiles/<name>/home
(see hermes_constants.get_subprocess_home()), so they read a different
.gitconfig / .git-credentials than the default profile. Before the section-4
per-profile loop, a $GITHUB_TOKEN rotation left every profile home on a STALE
(revoked) token and profile-scoped push/clone broke — the failure hand-fixed
for the finview profile on 2026-06-12.

This is a content-assertion test (matching tests/test_docker_home_override_
scripts.py): executing the real cont-init script needs root + s6-setuidgid +
git, none of which are available in CI. We assert the loop's invariants on the
script text instead.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BOOT_SCRIPT = REPO_ROOT / "docker" / "cont-init.d" / "03-biglobster-config"


@pytest.fixture(scope="module")
def boot_text() -> str:
    if not BOOT_SCRIPT.exists():
        pytest.skip("docker/cont-init.d/03-biglobster-config not present")
    return BOOT_SCRIPT.read_text(encoding="utf-8")


def _section4(boot_text: str) -> str:
    """Return just the section-4 git-credential block so assertions can't
    accidentally match the section-6 repo-sync git plumbing."""
    start = boot_text.index("--- 4:")
    end = boot_text.index("--- 5:")
    return boot_text[start:end]


def test_default_credential_still_written(boot_text: str) -> None:
    """The original default-profile behaviour is preserved."""
    s4 = _section4(boot_text)
    assert 'HOME="$HERMES_HOME" git config --global credential.helper store' in s4
    assert (
        "printf 'https://x-access-token:%s@github.com\\n' \"$GITHUB_TOKEN\" "
        '> "$HERMES_HOME/.git-credentials"'
    ) in s4


def test_per_profile_loop_iterates_profiles(boot_text: str) -> None:
    """Section 4 loops over $HERMES_HOME/profiles/*/ like sections 5 and 6."""
    s4 = _section4(boot_text)
    assert 'for prof_dir in "$HERMES_HOME/profiles"/*/' in s4


def test_per_profile_loop_guards_on_soul_marker(boot_text: str) -> None:
    """SOUL.md is the real-profile marker used everywhere else; a stray dir
    under profiles/ must be skipped."""
    s4 = _section4(boot_text)
    assert '[ -f "${prof_dir}SOUL.md" ] || continue' in s4


def test_per_profile_loop_creates_home_dir(boot_text: str) -> None:
    """§4 CREATES home/ for every real profile (PR #34) rather than skipping
    profiles that lack one: a profile onboarded before home/ was bootstrapped
    would otherwise silently never get git/gh auth (grow-shop, 2026-06-15)."""
    s4 = _section4(boot_text)
    assert 'prof_home="${prof_dir}home"' in s4
    assert 'as_hermes mkdir -p "$prof_home"' in s4


def test_per_profile_credential_written_under_profile_home(boot_text: str) -> None:
    """The credential helper + .git-credentials land under the profile's HOME,
    refreshed from the current $GITHUB_TOKEN."""
    s4 = _section4(boot_text)
    assert (
        'HOME="$prof_home" as_hermes git config --global credential.helper store'
        in s4
    )
    assert (
        "printf 'https://x-access-token:%s@github.com\\n' \"$GITHUB_TOKEN\" "
        '> "$prof_home/.git-credentials"'
    ) in s4
    assert 'chmod 600 "$prof_home/.git-credentials"' in s4
    assert 'chown hermes:hermes "$prof_home/.git-credentials"' in s4


def test_per_profile_writes_run_as_hermes(boot_text: str) -> None:
    """Profile homes are hermes-owned, so the git config writes drop to
    hermes via as_hermes (matching the rest of the script)."""
    s4 = _section4(boot_text)
    assert 'HOME="$prof_home" as_hermes git config' in s4


def test_per_profile_loop_is_non_fatal(boot_text: str) -> None:
    """A git failure for one profile must warn and continue, never abort
    boot (cont-init is never-fatal)."""
    s4 = _section4(boot_text)
    assert "(non-fatal)" in s4
