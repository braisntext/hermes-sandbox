"""Phase-1 integration lock: watcher proposal enrichment + remediate CLI.

Hermetic: jobs are injected synthetic records, the ledger is a tmp path, and the
cron fix (`trigger_job`) is monkeypatched so no real scheduler state is touched.
Guards the end-to-end gated flow: detect → propose-in-brief → apply → ledger,
plus the two safety behaviours (re-validate-live, debounce one-per-occurrence).
"""
from datetime import timedelta

import pytest

from incidents import sweep
from incidents.sweep import _now as now_fn
from remediation import cli, ledger


def _transient_job(jid="j1", err="Provider returned error (502)", ago_hours=1):
    last_run = (now_fn() - timedelta(hours=ago_hours)).isoformat()
    return {"id": jid, "name": "finview-cron", "last_error": err,
            "last_delivery_error": None, "last_run_at": last_run}


def _hard_fault_job(jid="j2"):
    last_run = now_fn().isoformat()
    return {"id": jid, "name": "broken-cron", "last_error": "ModuleNotFoundError: x",
            "last_delivery_error": None, "last_run_at": last_run}


@pytest.fixture
def patched_trigger(monkeypatch):
    """Record calls to cron.jobs.trigger_job instead of hitting the scheduler."""
    calls = []
    import cron.jobs as cj
    monkeypatch.setattr(cj, "trigger_job", lambda jid: (calls.append(jid) or {"id": jid}))
    return calls


class TestWatcherEnrichment:
    def test_transient_incident_brief_includes_proposal(self, tmp_path):
        out = sweep.sweep(jobs=[_transient_job()], langfuse=[], blocked=[],
                          state_path=tmp_path / "s.json")
        assert "Proposed remediation* (cron-transient-failure)" in out
        assert "python -m remediation.cli apply cron:j1:" in out

    def test_hard_fault_brief_has_no_proposal(self, tmp_path):
        out = sweep.sweep(jobs=[_hard_fault_job()], langfuse=[], blocked=[],
                          state_path=tmp_path / "s.json")
        assert "Incident" in out and "Proposed remediation" not in out


class TestList:
    def test_lists_transient_only(self, tmp_path):
        out = cli.cmd_list(jobs=[_transient_job(), _hard_fault_job()],
                           ledger_path=tmp_path / "l.jsonl")
        assert "cron-transient-failure" in out
        assert "broken-cron" not in out

    def test_empty_when_nothing_classifies(self, tmp_path):
        out = cli.cmd_list(jobs=[_hard_fault_job()], ledger_path=tmp_path / "l.jsonl")
        assert "No pending" in out

    def test_already_acted_signature_drops_from_list(self, tmp_path, patched_trigger):
        lp = tmp_path / "l.jsonl"
        jobs = [_transient_job()]
        sig = cli._live_incidents(jobs)[0].id
        cli.cmd_apply(sig, jobs=jobs, ledger_path=lp)          # act once
        out = cli.cmd_list(jobs=jobs, ledger_path=lp)          # now debounced out
        assert "No pending" in out


class TestApply:
    def test_apply_runs_fix_and_logs(self, tmp_path, patched_trigger):
        lp = tmp_path / "l.jsonl"
        jobs = [_transient_job()]
        sig = cli._live_incidents(jobs)[0].id
        code, msg = cli.cmd_apply(sig, jobs=jobs, ledger_path=lp)
        assert code == 0 and "applied" in msg
        assert patched_trigger == ["j1"]
        rows = ledger.read(path=lp)
        assert len(rows) == 1
        assert rows[0].event == ledger.EVENT_APPLIED
        assert rows[0].outcome == ledger.OUTCOME_SUCCESS
        assert rows[0].signature == sig

    def test_apply_revalidates_live_resolved_failure(self, tmp_path, patched_trigger):
        # Signature not among live incidents -> already resolved -> no action, no log.
        code, msg = cli.cmd_apply("cron:ghost:2026-01-01T00:00:00+00:00",
                                  jobs=[], ledger_path=tmp_path / "l.jsonl")
        assert code == 0 and "already resolved" in msg.lower()
        assert patched_trigger == []
        assert ledger.read(path=tmp_path / "l.jsonl") == []

    def test_apply_debounces_second_attempt(self, tmp_path, patched_trigger):
        lp = tmp_path / "l.jsonl"
        jobs = [_transient_job()]
        sig = cli._live_incidents(jobs)[0].id
        cli.cmd_apply(sig, jobs=jobs, ledger_path=lp)
        code, msg = cli.cmd_apply(sig, jobs=jobs, ledger_path=lp)   # same occurrence
        assert code == 1 and "already applied" in msg.lower()
        assert patched_trigger == ["j1"]                            # fix ran only once

    def test_apply_unresolvable_job_id_logs_failure(self, tmp_path, monkeypatch):
        import cron.jobs as cj
        monkeypatch.setattr(cj, "trigger_job", lambda jid: None)   # job not found
        lp = tmp_path / "l.jsonl"
        jobs = [_transient_job()]
        sig = cli._live_incidents(jobs)[0].id
        code, msg = cli.cmd_apply(sig, jobs=jobs, ledger_path=lp)
        assert code == 1 and "FAILED" in msg
        rows = ledger.read(path=lp)
        assert rows[0].outcome == ledger.OUTCOME_FAILURE
