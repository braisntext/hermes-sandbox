"""Regression lock for the self-remediation Phase-0 brakes.

Hermetic: every test injects ``path`` (tmp ledger), ``now`` and ``env`` so no real
disk, clock or environment is touched. Guards the three independent refusals
(kill switch, debounce, rate limit), the ledger roundtrip, and malformed-line
tolerance — the invariants that keep an auto-act bounded and reversible.
"""
from datetime import timedelta

from remediation import ledger
from remediation.ledger import LedgerEntry, make_entry
from remediation.guards import (
    GATE_DEBOUNCE, GATE_KILLSWITCH, GATE_OK, GATE_RATELIMIT,
    autonomy_paused, may_auto_act,
)

NOW = ledger._now()
CLS = "cron-transient-failure"
SIG = "cron:j1:2026-06-26T10:00:00+00:00"


def _applied(cls=CLS, sig=SIG, ago_hours=1):
    return make_entry(cls, sig, "job j1", "auto", ledger.EVENT_APPLIED,
                      now=NOW - timedelta(hours=ago_hours))


class TestLedgerIO:
    def test_append_then_read_roundtrip(self, tmp_path):
        p = tmp_path / "ledger.jsonl"
        ledger.append(_applied(), path=p)
        ledger.append(make_entry(CLS, SIG, "job j1", "auto", ledger.EVENT_VERIFIED,
                                  outcome=ledger.OUTCOME_SUCCESS), path=p)
        rows = ledger.read(path=p)
        assert [r.event for r in rows] == [ledger.EVENT_APPLIED, ledger.EVENT_VERIFIED]
        assert rows[0].cls == CLS and rows[0].signature == SIG

    def test_missing_file_reads_empty(self, tmp_path):
        assert ledger.read(path=tmp_path / "nope.jsonl") == []

    def test_malformed_lines_are_skipped(self, tmp_path):
        p = tmp_path / "ledger.jsonl"
        ledger.append(_applied(), path=p)
        with p.open("a", encoding="utf-8") as fh:
            fh.write("not json\n\n{\"missing\": \"keys\"}\n")
        rows = ledger.read(path=p)
        assert len(rows) == 1 and rows[0].event == ledger.EVENT_APPLIED

    def test_prune_keeps_most_recent(self, tmp_path):
        p = tmp_path / "ledger.jsonl"
        for i in range(5):
            ledger.append(make_entry(CLS, f"sig{i}", "t", "auto", ledger.EVENT_APPLIED), path=p, cap=3)
        rows = ledger.read(path=p)
        assert len(rows) == 3
        assert [r.signature for r in rows] == ["sig2", "sig3", "sig4"]


class TestDebounce:
    def test_recent_action_debounces(self, tmp_path):
        entries = [_applied(ago_hours=1)]
        assert ledger.recently_acted(SIG, entries=entries, now=NOW) is True

    def test_old_action_does_not_debounce(self, tmp_path):
        entries = [_applied(ago_hours=ledger.DEBOUNCE_HOURS + 1)]
        assert ledger.recently_acted(SIG, entries=entries, now=NOW) is False

    def test_different_signature_does_not_debounce(self):
        entries = [_applied(sig="other-sig")]
        assert ledger.recently_acted(SIG, entries=entries, now=NOW) is False

    def test_only_applied_events_debounce(self):
        # A mere proposal must NOT debounce a real fix.
        proposal = make_entry(CLS, SIG, "t", "gated", ledger.EVENT_PROPOSED, now=NOW)
        assert ledger.recently_acted(SIG, entries=[proposal], now=NOW) is False


class TestRateLimit:
    def test_counts_only_applied_within_window_and_class(self):
        entries = [
            _applied(sig="a", ago_hours=1),
            _applied(sig="b", ago_hours=2),
            _applied(sig="c", cls="other-class", ago_hours=1),       # different class
            _applied(sig="d", ago_hours=ledger.RATE_WINDOW_HOURS + 1),  # too old
            make_entry(CLS, "e", "t", "auto", ledger.EVENT_PROPOSED, now=NOW),  # not an action
        ]
        assert ledger.act_count(CLS, entries=entries, now=NOW) == 2


class TestKillSwitch:
    def test_paused_values_engage(self):
        for v in ("paused", "PAUSED", " off ", "Frozen", "stop"):
            assert autonomy_paused({"HERMES_AUTONOMY": v}) is True

    def test_unset_or_active_is_open(self):
        assert autonomy_paused({}) is False
        assert autonomy_paused({"HERMES_AUTONOMY": "active"}) is False


class TestMayAutoAct:
    def test_clean_state_allows(self):
        d = may_auto_act(CLS, SIG, entries=[], now=NOW, env={})
        assert d.allowed and d.reason == GATE_OK

    def test_killswitch_refuses_before_anything_else(self):
        # Even with a debounce hit present, the kill switch is the outermost refusal.
        d = may_auto_act(CLS, SIG, entries=[_applied()], now=NOW,
                         env={"HERMES_AUTONOMY": "paused"})
        assert not d.allowed and d.reason == GATE_KILLSWITCH

    def test_debounce_refuses(self):
        d = may_auto_act(CLS, SIG, entries=[_applied(ago_hours=1)], now=NOW, env={})
        assert not d.allowed and d.reason == GATE_DEBOUNCE

    def test_rate_limit_refuses(self):
        # rate_max distinct signatures for the class, none matching SIG (so debounce passes).
        entries = [_applied(sig=f"s{i}", ago_hours=1) for i in range(ledger.RATE_MAX_PER_CLASS)]
        d = may_auto_act(CLS, "fresh-sig", entries=entries, now=NOW, env={})
        assert not d.allowed and d.reason == GATE_RATELIMIT
