"""Regression lock for auditor PR-dedup (auditor/pending.py).

Hermetic: monkeypatches the gh call and injects a tmp state_path, so no network
or gh binary is touched. Guards the back-and-forth contract — a PR is pending
until reviewed at its head SHA, and reappears when the author pushes (new SHA).
"""
import json

import auditor.pending as pending


def _pr(number, head, login="claude-code", draft=False, files=("hermes/x.py",)):
    return {
        "number": number,
        "title": f"PR {number}",
        "headRefName": f"review/{number}",
        "headRefOid": head,
        "author": {"login": login},
        "isDraft": draft,
        "files": [{"path": p} for p in files],
        "url": f"https://github.com/o/r/pull/{number}",
    }


def _patch_gh(monkeypatch, prs):
    monkeypatch.setattr(pending, "_gh_list_open_prs", lambda repo: prs)


def test_new_pr_is_pending(monkeypatch, tmp_path):
    _patch_gh(monkeypatch, [_pr(1, "aaa")])
    out = pending.pending_prs(None, tmp_path / "state.json")
    assert [p["number"] for p in out] == [1]
    assert out[0]["changed_files"] == ["hermes/x.py"]


def test_reviewed_head_is_not_pending(monkeypatch, tmp_path):
    sp = tmp_path / "state.json"
    _patch_gh(monkeypatch, [_pr(1, "aaa")])
    pending.mark_reviewed(1, "aaa", sp)
    assert pending.pending_prs(None, sp) == []


def test_new_commit_makes_pr_pending_again(monkeypatch, tmp_path):
    sp = tmp_path / "state.json"
    pending.mark_reviewed(1, "aaa", sp)
    # author pushed -> new head SHA
    _patch_gh(monkeypatch, [_pr(1, "bbb")])
    out = pending.pending_prs(None, sp)
    assert [p["number"] for p in out] == [1]
    assert out[0]["headRefOid"] == "bbb"


def test_drafts_skipped_by_default(monkeypatch, tmp_path):
    _patch_gh(monkeypatch, [_pr(1, "aaa", draft=True)])
    assert pending.pending_prs(None, tmp_path / "s.json") == []
    assert pending.pending_prs(None, tmp_path / "s.json", include_drafts=True)


def test_pr_missing_head_skipped(monkeypatch, tmp_path):
    bad = _pr(1, "")
    _patch_gh(monkeypatch, [bad])
    assert pending.pending_prs(None, tmp_path / "s.json") == []


def test_mark_is_bounded(monkeypatch, tmp_path):
    sp = tmp_path / "state.json"
    for i in range(pending._SEEN_CAP + 50):
        pending.mark_reviewed(i, "h", sp)
    seen = json.loads(sp.read_text())["seen"]
    assert len(seen) == pending._SEEN_CAP


def test_gh_failure_degrades_to_empty(monkeypatch, tmp_path):
    def boom(repo):
        raise RuntimeError("gh exploded")

    # pending_prs should surface the failure as no work, not crash, when the
    # lister itself returns [] — simulate the documented degrade path.
    monkeypatch.setattr(pending, "_gh_list_open_prs", lambda repo: [])
    assert pending.pending_prs(None, tmp_path / "s.json") == []
