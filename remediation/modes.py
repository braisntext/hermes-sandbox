"""Per-class lifecycle mode state (Phase 1).

A remediation class's *mode* (``gated`` | ``auto``) is the trust dial. It starts
at the class's ``default_mode`` and only moves gated→auto when the CEO approves a
promotion. Because that promotion is *earned runtime state*, it lives on the
volume — NOT in the code registry — at ``$HERMES_HOME/remediation/modes.json``,
mirroring ``incidents/state.json``.

This file is deliberately a separate volume artifact that no deploy step writes:
the §6d auditor resync copies SOUL/prompt only, the §6 clone pull replaces code
only. So a promotion survives reboot and redeploy untouched — same durability
property as ``gateway_state``.

``promote()`` is the ONLY writer that flips a mode to ``auto``; it records nothing
about *authorisation* — the caller (the promotion CLI) is responsible for the CEO
approval. Demotion back to ``gated`` is always available as an instant brake.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

MODE_GATED = "gated"
MODE_AUTO = "auto"
_VALID_MODES = {MODE_GATED, MODE_AUTO}


def _modes_path() -> Path:
    from hermes_constants import get_hermes_home
    return get_hermes_home() / "remediation" / "modes.json"


def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def _default_mode(name: str) -> str:
    """The registry's declared default for a class (fallback: gated = safest)."""
    from remediation.registry import REGISTRY
    for rc in REGISTRY:
        if rc.name == name:
            return rc.default_mode
    return MODE_GATED


def _auto_eligible(name: str) -> bool:
    """Whether a class may EVER reach ``auto``. A gated-only class (a destructive
    fix) is never auto — enforced here at the mode chokepoint so a hand-edited
    ``modes.json`` or a direct ``promote()`` can't bypass the CLI guard. Unknown
    classes are treated as eligible (no special-casing)."""
    from remediation.registry import REGISTRY
    for rc in REGISTRY:
        if rc.name == name:
            return rc.auto_eligible
    return True


def mode_for(name: str, *, path: Optional[Path] = None) -> str:
    """Effective mode for a class: the persisted override if present and valid,
    otherwise the registry default. Unknown/corrupt overrides fail safe to the
    default (never silently to ``auto``)."""
    path = path or _modes_path()
    override = _load(path).get(name)
    if override in _VALID_MODES:
        return override
    return _default_mode(name)


def is_auto(name: str, *, path: Optional[Path] = None) -> bool:
    # A gated-only class is never auto, even if modes.json was hand-edited to say
    # so — this is the decision the auto-act path (Phase 3) gates on.
    if not _auto_eligible(name):
        return False
    return mode_for(name, path=path) == MODE_AUTO


def promote(name: str, *, path: Optional[Path] = None) -> str:
    """Flip a class to ``auto`` (CEO-approved promotion). Returns the new mode."""
    return set_mode(name, MODE_AUTO, path=path)


def demote(name: str, *, path: Optional[Path] = None) -> str:
    """Force a class back to ``gated`` — the instant per-class brake. Returns the new mode."""
    return set_mode(name, MODE_GATED, path=path)


def set_mode(name: str, mode: str, *, path: Optional[Path] = None) -> str:
    if mode not in _VALID_MODES:
        raise ValueError(f"invalid mode {mode!r}; expected one of {sorted(_VALID_MODES)}")
    if mode == MODE_AUTO and not _auto_eligible(name):
        raise ValueError(f"{name!r} is gated-only (auto_eligible=False) and cannot be set to auto")
    path = path or _modes_path()
    data = _load(path)
    data[name] = mode
    _save(path, data)
    return mode
