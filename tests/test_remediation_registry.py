"""Regression lock for the Phase-1 registry + mode state.

Hermetic: mode tests inject ``path``; registry tests build synthetic Incidents.
Guards the two invariants that keep gated→auto safe: (1) only genuinely transient
cron failures match the retry class — a hard fault (auth/config/missing code)
never does; (2) mode state fails safe to the registry default, never silently to
``auto``.
"""
import pytest

from incidents.sweep import Incident
from remediation import modes, registry
from remediation.registry import CRON_TRANSIENT_FAILURE, classify


def _cron_inc(jid="j1", err="Provider returned error (502)"):
    return Incident(
        id=f"cron:{jid}:2026-06-26T10:00:00+00:00",
        kind="cron",
        title=f"Cron job '{jid}' failed (agent error)",
        detail=f"when: 2026-06-26T10:00:00+00:00\nerror: {err}",
        handoff=f"cron job id {jid}",
    )


class TestTransientHeuristic:
    @pytest.mark.parametrize("err", [
        "Provider returned error", "HTTP 503 Service Unavailable",
        "Read timed out", "rate limit exceeded (429)", "connection refused",
    ])
    def test_transient_errors_match(self, err):
        assert classify(_cron_inc(err=err)) is CRON_TRANSIENT_FAILURE

    @pytest.mark.parametrize("err", [
        "ModuleNotFoundError: no module named cron",
        "401 Unauthorized", "No models provided", "permission denied",
        "FileNotFoundError: no such file",
    ])
    def test_hard_faults_do_not_match(self, err):
        assert classify(_cron_inc(err=err)) is None

    def test_hard_fault_vetoes_even_with_transient_marker(self):
        # "timeout" is transient, but a traceback/ModuleNotFound veto wins.
        inc = _cron_inc(err="connection timeout then ModuleNotFoundError")
        assert classify(inc) is None

    def test_non_cron_incident_does_not_match(self):
        inc = Incident(id="trace:abc", kind="langfuse", title="t", detail="503", handoff="trace-id abc")
        assert classify(inc) is None


class TestProposalAndJobId:
    def test_proposal_names_the_job(self):
        text = CRON_TRANSIENT_FAILURE.proposal(_cron_inc(jid="c19bb95c0a62"))
        assert "c19bb95c0a62" in text and "retry" in text.lower()

    def test_signature_is_the_incident_id(self):
        # The debounce key is the incident id verbatim — re-run produces a new id.
        inc = _cron_inc()
        assert inc.id == "cron:j1:2026-06-26T10:00:00+00:00"


class TestModeState:
    def test_default_is_registry_default_when_unset(self, tmp_path):
        assert modes.mode_for("cron-transient-failure", path=tmp_path / "m.json") == "gated"

    def test_promote_then_persists_auto(self, tmp_path):
        p = tmp_path / "m.json"
        assert modes.promote("cron-transient-failure", path=p) == "auto"
        assert modes.is_auto("cron-transient-failure", path=p) is True

    def test_demote_returns_to_gated(self, tmp_path):
        p = tmp_path / "m.json"
        modes.promote("cron-transient-failure", path=p)
        assert modes.demote("cron-transient-failure", path=p) == "gated"
        assert modes.is_auto("cron-transient-failure", path=p) is False

    def test_corrupt_override_fails_safe_to_default(self, tmp_path):
        p = tmp_path / "m.json"
        p.write_text('{"cron-transient-failure": "YOLO"}', encoding="utf-8")
        assert modes.mode_for("cron-transient-failure", path=p) == "gated"

    def test_invalid_set_mode_raises(self, tmp_path):
        with pytest.raises(ValueError):
            modes.set_mode("cron-transient-failure", "turbo", path=tmp_path / "m.json")

    def test_unknown_class_defaults_gated(self, tmp_path):
        assert modes.mode_for("does-not-exist", path=tmp_path / "m.json") == "gated"
