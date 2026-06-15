"""Contract test: docker/cont-init.d/03-biglobster-config reliably resolves a
GitHub token and propagates it as BOTH ``GITHUB_TOKEN`` and ``GH_TOKEN``.

Root cause this guards against: in the Zeabur deployment GITHUB_TOKEN is not a
platform-injected process env var — it lives only in ``$HERMES_HOME/.env``,
historically as duplicate, divergent lines (a stale classic ``ghp_…`` PAT plus
the valid fine-grained ``github_pat_…`` one). Because the old boot hook gated
its env sync (§1) and git-credential write (§4) on a non-empty process-env
value, both silently skipped GITHUB_TOKEN every boot: the divergent .env lines
were never deduped and the gateway's load_dotenv (last-occurrence-wins) could
load a revoked token, while no GH_TOKEN was ever produced at all.

These are content assertions on the script text (matching
``test_biglobster_git_credentials.py``): executing the real cont-init script
needs root + s6-setuidgid, neither available in CI. The dedupe semantics of the
embedded ``_sync_env_file`` are additionally exercised functionally below by
replicating the function in-process.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BOOT_SCRIPT = REPO_ROOT / "docker" / "cont-init.d" / "03-biglobster-config"


@pytest.fixture(scope="module")
def boot_text() -> str:
    if not BOOT_SCRIPT.exists():
        pytest.skip("docker/cont-init.d/03-biglobster-config not present")
    return BOOT_SCRIPT.read_text(encoding="utf-8")


def test_token_resolution_reads_from_env_file(boot_text: str) -> None:
    """When the process env carries no token, the hook sources it from the last
    GITHUB_TOKEN (then GH_TOKEN) line in $HERMES_HOME/.env."""
    assert 'grep -E \'^GITHUB_TOKEN=\' "$HERMES_HOME/.env"' in boot_text
    assert 'grep -E \'^GH_TOKEN=\' "$HERMES_HOME/.env"' in boot_text
    assert "tail -n1" in boot_text


def test_token_resolution_prefers_process_env(boot_text: str) -> None:
    """An explicit process-env GITHUB_TOKEN/GH_TOKEN is authoritative and used
    before falling back to the .env file."""
    # The .env read is guarded on GITHUB_TOKEN already being empty.
    assert 'if [ -z "${GITHUB_TOKEN:-}" ] && [ -f "$HERMES_HOME/.env" ]; then' in boot_text
    # GH_TOKEN in the process env can stand in for a missing GITHUB_TOKEN.
    assert 'GITHUB_TOKEN="${GH_TOKEN:-}"' in boot_text


def test_token_is_exported_under_both_names(boot_text: str) -> None:
    """Both names are exported so §1's python, §4's git config, and the
    gateway/delegate process env (via load_dotenv on the synced .env) agree."""
    assert 'GH_TOKEN="$GITHUB_TOKEN"' in boot_text
    assert "export GITHUB_TOKEN GH_TOKEN" in boot_text


def test_inject_list_includes_both_token_names(boot_text: str) -> None:
    """§1 syncs GITHUB_TOKEN and GH_TOKEN into the main and per-profile .env."""
    m = re.search(r"inject = \[(.*?)\]", boot_text, re.DOTALL)
    assert m, "inject list not found"
    inject_block = m.group(1)
    assert '"GITHUB_TOKEN"' in inject_block
    assert '"GH_TOKEN"' in inject_block


# --- functional check of the embedded _sync_env_file dedupe semantics --------
# Replicated verbatim from the §1 heredoc; if the heredoc changes, update here.
_INJECT = [
    "OPENROUTER_API_KEY", "HERMES_CALLBACK_SECRET", "HERMES_CALLBACK_URL",
    "HERMES_MAX_ITERATIONS", "EXA_API_KEY", "HUGGINGFACE_API_KEY",
    "GITHUB_TOKEN", "GH_TOKEN", "AUXILIARY_VISION_MODEL",
]


def _sync_env_file_content(content: str, environ: dict) -> str:
    for var in _INJECT:
        val = environ.get(var, "")
        if not val:
            continue
        line_re = rf"^{re.escape(var)}=.*$"
        matches = re.findall(line_re, content, flags=re.MULTILINE)
        if len(matches) == 1:
            content = re.sub(line_re, lambda _m: f"{var}={val}", content, flags=re.MULTILINE)
        elif len(matches) > 1:
            content = re.sub(rf"^{re.escape(var)}=.*(?:\n|$)", "", content, flags=re.MULTILINE)
            if content and not content.endswith("\n"):
                content += "\n"
            content += f"{var}={val}\n"
        else:
            sep = "" if (not content or content.endswith("\n")) else "\n"
            content += f"{sep}{var}={val}\n"
    return content


def test_sync_collapses_divergent_duplicates() -> None:
    """The prod failure mode: two divergent GITHUB_TOKEN lines collapse to one
    canonical line (the valid, last one) and the stale one is removed."""
    prod = (
        "OPENROUTER_API_KEY=sk-or-old\n"
        "GITHUB_TOKEN=ghp_STALE\n"
        "EXA_API_KEY=exa\n"
        "GITHUB_TOKEN=github_pat_VALID\n"
    )
    env = {"GITHUB_TOKEN": "github_pat_VALID", "GH_TOKEN": "github_pat_VALID"}
    out = _sync_env_file_content(prod, env)
    assert re.findall(r"^GITHUB_TOKEN=.*$", out, re.MULTILINE) == ["GITHUB_TOKEN=github_pat_VALID"]
    assert re.findall(r"^GH_TOKEN=.*$", out, re.MULTILINE) == ["GH_TOKEN=github_pat_VALID"]
    assert "ghp_STALE" not in out


def test_sync_is_idempotent() -> None:
    env = {"GITHUB_TOKEN": "github_pat_VALID", "GH_TOKEN": "github_pat_VALID"}
    once = _sync_env_file_content("GITHUB_TOKEN=ghp_a\nGITHUB_TOKEN=ghp_b\n", env)
    twice = _sync_env_file_content(once, env)
    assert once == twice


def test_sync_single_line_preserves_position() -> None:
    """A single existing line is replaced in place — no reordering churn."""
    single = "A=1\nGITHUB_TOKEN=ghp_x\nB=2\n"
    out = _sync_env_file_content(single, {"GITHUB_TOKEN": "ghp_x"})
    assert out == single
