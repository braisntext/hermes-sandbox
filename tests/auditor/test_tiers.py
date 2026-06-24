"""Regression lock for auditor risk-tiering (auditor/tiers.py).

The gate's depth-of-review hinges on this. The load-bearing property is the
fail-safe: anything system OR unrecognised must classify as ``system`` — a weak
gate that rubber-stamps unknown paths is worse than an over-eager one.
"""
from auditor.tiers import classify, unknown_paths


def test_system_code_paths_are_system():
    for p in [
        "hermes/agent.py",
        "cron/scheduler.py",
        "gateway/poller.py",
        "docker/cont-init.d/03-biglobster-config",
        "scripts/git-guard/pre-commit",
        "tools/cronjob_tools.py",
        "evals/run.py",
        "tests/test_x.py",
    ]:
        assert classify([p]) == "system", p


def test_root_files_and_modules_are_system():
    assert classify(["cli.py"]) == "system"
    assert classify(["pyproject.toml"]) == "system"
    assert classify(["Dockerfile"]) == "system"
    assert classify(["docker-compose.windows.yml"]) == "system"
    assert classify(["some_top_level.py"]) == "system"


def test_pure_content_is_content():
    assert classify(["docs/guide.md"]) == "content"
    assert classify(["README.md"]) == "content"
    assert classify(["website/index.html"]) == "content"
    assert classify(["web/blog/post.html", "docs/x.md"]) == "content"


def test_mixed_system_and_content_is_system():
    assert classify(["docs/guide.md", "hermes/agent.py"]) == "system"


def test_prompt_files_are_system_at_any_depth():
    # *.prompt is autonomous-agent behaviour -> always strong reviewer, not via
    # the unknown-path fail-safe but explicitly.
    assert classify(["offsite-geo/geo-scout.prompt"]) == "system"
    assert classify(["infographic/infographic-engineer.prompt"]) == "system"
    assert classify(["auditor/auditor.prompt"]) == "system"
    assert classify(["some/random/dir/x.prompt"]) == "system"
    assert classify(["top.prompt"]) == "system"


def test_known_prompt_dirs_are_system():
    assert classify(["offsite-geo/anything.txt"]) == "system"
    assert classify(["infographic/notes.md"]) == "system"


def test_prompt_files_not_surfaced_as_unknown():
    # Now recognised, so they must NOT appear in unknown_paths.
    assert unknown_paths(["offsite-geo/geo-scout.prompt"]) == []


def test_unknown_path_fails_safe_to_system():
    # Not in either list => must be system, not content.
    assert classify(["weird/unknown_dir/file.bin"]) == "system"


def test_empty_changeset_is_content():
    assert classify([]) == "content"
    assert classify(["", "  "]) == "content"


def test_leading_dot_slash_normalised():
    assert classify(["./hermes/agent.py"]) == "system"
    assert classify(["./README.md"]) == "content"


def test_unknown_paths_surfaced():
    paths = ["hermes/agent.py", "docs/x.md", "weird/thing.bin"]
    assert unknown_paths(paths) == ["weird/thing.bin"]
