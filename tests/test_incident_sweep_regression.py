"""Regression lock for the incident watcher sweep (incidents/sweep.py).

Hermetic: injects synthetic jobs / langfuse incidents / now / state_path, so no
cron data, network, or clock is touched. Guards the configured behaviour —
detect failures, dedup, silent-when-clean, and the 24h heartbeat.
"""
from datetime import timedelta

from incidents.sweep import Incident, cron_failure_incidents, sweep
from incidents.sweep import _now as now_fn


def _failed_job(jid="j1", name="finview-cron", agent_err="boom", delivery_err=None, ago_hours=1):
    last_run = (now_fn() - timedelta(hours=ago_hours)).isoformat()
    return {"id": jid, "name": name, "last_error": agent_err,
            "last_delivery_error": delivery_err, "last_run_at": last_run}


def _ok_job(jid="ok1"):
    return {"id": jid, "name": "healthy", "last_error": None,
            "last_delivery_error": None, "last_run_at": now_fn().isoformat()}


class TestDetection:
    def test_failed_job_becomes_an_incident(self):
        incs = cron_failure_incidents([_failed_job()])
        assert len(incs) == 1
        assert "finview-cron" in incs[0].title
        assert incs[0].handoff == "cron job id j1"

    def test_healthy_job_is_ignored(self):
        assert cron_failure_incidents([_ok_job()]) == []

    def test_stale_failure_outside_window_is_ignored(self):
        assert cron_failure_incidents([_failed_job(ago_hours=72)]) == []

    def test_delivery_error_is_flagged(self):
        incs = cron_failure_incidents([_failed_job(agent_err=None, delivery_err="telegram 502")])
        assert len(incs) == 1 and "delivery error" in incs[0].title


class TestSweepBehaviour:
    def test_clean_first_run_emits_heartbeat(self, tmp_path):
        # No incidents, no prior state -> first run proves it's alive.
        out = sweep(jobs=[_ok_job()], langfuse=[], state_path=tmp_path / "s.json")
        assert "still running" in out

    def test_clean_run_after_recent_heartbeat_is_silent(self, tmp_path):
        sp = tmp_path / "s.json"
        sweep(jobs=[_ok_job()], langfuse=[], state_path=sp)          # emits heartbeat, records ts
        out = sweep(jobs=[_ok_job()], langfuse=[], state_path=sp)    # within 24h -> silent
        assert out == ""

    def test_heartbeat_returns_after_24h_of_silence(self, tmp_path):
        sp = tmp_path / "s.json"
        sweep(jobs=[_ok_job()], langfuse=[], state_path=sp)
        later = now_fn() + timedelta(hours=25)
        out = sweep(jobs=[_ok_job()], langfuse=[], state_path=sp, now=later)
        assert "still running" in out

    def test_incident_is_reported_then_deduped(self, tmp_path):
        sp = tmp_path / "s.json"
        job = _failed_job()  # same record (stable last_run_at) across both sweeps
        first = sweep(jobs=[job], langfuse=[], state_path=sp)
        assert "Incident" in first and "finview-cron" in first
        second = sweep(jobs=[job], langfuse=[], state_path=sp)  # same failure, same run
        assert second == ""  # deduped, and not yet heartbeat-due

    def test_langfuse_incident_is_included(self, tmp_path):
        lf = [Incident(id="trace:abc", kind="langfuse", title="Langfuse error trace abc…",
                       detail="signal: boom", handoff="trace-id abc")]
        out = sweep(jobs=[_ok_job()], langfuse=lf, state_path=tmp_path / "s.json")
        assert "trace-id abc" in out

    def test_dry_run_does_not_persist_state(self, tmp_path):
        sp = tmp_path / "s.json"
        sweep(jobs=[_failed_job()], langfuse=[], state_path=sp, dry_run=True)
        assert not sp.exists()  # nothing written -> next real run still reports it
        out = sweep(jobs=[_failed_job()], langfuse=[], state_path=sp)
        assert "Incident" in out
