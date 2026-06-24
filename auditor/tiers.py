"""Risk-tiering for the auditor: classify a PR's changed files.

Two tiers drive how hard the auditor reviews and which model it spends:

  * ``system``  — code/config that can break Hermes itself or a profile's runtime.
                  Deep review with the strong paid model; hard gate.
  * ``content`` — docs, website copy, marketing/blog assets. Light review with the
                  cheap model; the existing asset guards already cover the
                  catastrophic case (the cover-wipe).

Policy is **fail-safe toward more review**: a PR is ``system`` if ANY changed file
matches a system pattern, OR if any file matches neither list (unknown => treat as
system). It is ``content`` only when EVERY changed file is recognisably content.
A weak gate that rubber-stamps unknown paths is worse than an over-eager one.

The lists are deliberately path-prefix / suffix based (no real globbing) so the
classification is obvious and cheap to reason about. Tune them in one place here.

SCOPE: these globs are tuned for the ``hermes-sandbox`` engine repo. Profile repos
(biglobster, FinView, grow-shop-*, SocialAgenda) have different layouts; on those,
unknown paths fall through to ``system`` (fail-safe, over-reviews). Per-repo tier
profiles keyed by repo slug are Phase 4 — not yet implemented.
"""
from __future__ import annotations

from typing import Iterable, List

# --- system: anything that can change runtime behaviour ---------------------
# Directory prefixes (matched against the repo-relative POSIX path).
_SYSTEM_DIR_PREFIXES = (
    "hermes/",
    "hermes_cli/",
    "cron/",
    "gateway/",
    "tui_gateway/",
    "acp_adapter/",
    "acp_registry/",
    "providers/",
    "plugins/",
    "skills/",
    "tools/",
    "toolsets/",
    "evals/",
    "incidents/",
    "auditor/",
    "agent/",
    "apps/",
    "docker/",
    "scripts/",
    "nix/",
    "packaging/",
    "tests/",
    # Agent-prompt dirs: a cron/agent prompt IS autonomous behaviour, so it
    # warrants the strong reviewer (a bad prompt is the cover-wipe class of
    # risk). The .prompt suffix rule below also catches prompts in any dir.
    "offsite-geo/",
    "infographic/",
)
# Exact root-level files that are system config.
_SYSTEM_ROOT_FILES = (
    "Dockerfile",
    "cloudbuild.yaml",
    "pyproject.toml",
    "setup.py",
    "package.json",
    "package-lock.json",
    "uv.lock",
    "flake.nix",
    "flake.lock",
    "zbpack.json",
    "cli.py",
    "run_agent.py",
    "mcp_serve.py",
    "batch_runner.py",
)
# Root-level filename suffixes / prefixes that are system config.
_SYSTEM_ROOT_PREFIXES = ("docker-compose", "constraints-")
_SYSTEM_ROOT_SUFFIXES = (".py",)  # any top-level *.py is module code
# Suffixes that mark a file system-tier at ANY path depth. A *.prompt file is an
# autonomous agent's instructions — behaviour, not prose — so it always gets the
# strong reviewer regardless of which dir it lives in (offsite-geo, infographic,
# auditor, or a future one). This makes the prior unknown-path fail-safe explicit.
_SYSTEM_ANY_SUFFIXES = (".prompt",)

# --- content: docs, site, copy, assets --------------------------------------
_CONTENT_DIR_PREFIXES = (
    "web/",
    "website/",
    "docs/",
    "assets/",
    "locales/",
    "datagen-config-examples/",
)
_CONTENT_SUFFIXES = (".md", ".mdx", ".txt", ".rst")


def _is_system(path: str) -> bool:
    p = path.strip().lstrip("./")
    if not p:
        return False
    if any(p.startswith(prefix) for prefix in _SYSTEM_DIR_PREFIXES):
        return True
    if any(p.endswith(suf) for suf in _SYSTEM_ANY_SUFFIXES):
        return True
    # root-level file (no slash) checks
    if "/" not in p:
        if p in _SYSTEM_ROOT_FILES:
            return True
        if any(p.startswith(pre) for pre in _SYSTEM_ROOT_PREFIXES):
            return True
        if any(p.endswith(suf) for suf in _SYSTEM_ROOT_SUFFIXES):
            return True
    return False


def _is_content(path: str) -> bool:
    p = path.strip().lstrip("./")
    if not p:
        return False
    if any(p.startswith(prefix) for prefix in _CONTENT_DIR_PREFIXES):
        return True
    if any(p.endswith(suf) for suf in _CONTENT_SUFFIXES):
        return True
    return False


def classify(paths: Iterable[str]) -> str:
    """Return ``"system"`` or ``"content"`` for a set of changed file paths.

    Fail-safe: ``system`` wins if any path is system OR unrecognised. ``content``
    only when every path is recognisably content. An empty changeset is treated
    as ``content`` (nothing to break).
    """
    paths = [p for p in (paths or []) if p and p.strip()]
    if not paths:
        return "content"
    for p in paths:
        if _is_system(p):
            return "system"
    # No system path matched — only content if EVERY remaining path is content.
    if all(_is_content(p) for p in paths):
        return "content"
    return "system"  # unknown path(s) => fail safe


def unknown_paths(paths: Iterable[str]) -> List[str]:
    """Paths that match neither list — surfaced so the tiers can be tuned."""
    out = []
    for p in paths or []:
        if not p or not p.strip():
            continue
        if not _is_system(p) and not _is_content(p):
            out.append(p.strip().lstrip("./"))
    return out
