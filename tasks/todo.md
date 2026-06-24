# Auditor Agent — implementation plan

A second-LLM technical reviewer that validates and improves changes before they
reach `main`, able to converse with the originating agent via the PR thread.

## Locked decisions
- **Identity:** dedicated GitHub bot account `hermes-auditor` (own PAT, repo write).
  Distinct author; future-proofs branch protection.
- **Model:** tiered — strong paid model (OpenRouter) for system-code PRs,
  free/cheap (owl-alpha tier) for content PRs.
- **Pilot scope:** hermes repo only, live agents (Claude Code + Telegram Hermes),
  hard gate on system code. Dead-cron "who fixes" deferred to phase 2.

## Architecture (locked earlier)
- Changes to hermes repo go via `review/*` branch + PR — never direct to `main`.
- New Hermes profile `auditor` runs an **agent** cron that polls open PRs.
- Per PR: classify risk by changed paths → review → comment / push fix commits /
  approve+merge / escalate to Telegram thread. `gh pr merge` is server-side, so it
  bypasses local hooks (no self-push-to-main needed).
- Reuses: incident-watcher polling+state pattern, Telegram thread routing,
  per-profile `.env` wiring in `docker/cont-init.d/03-biglobster-config`.

## Constraints (from repo, non-negotiable)
- GitHub Free + private: NO Actions, NO server-side hooks. Polling only.
- jobs.json lives on the volume; cron create needs profile/workdir via CLI.
- Repo `.prompt` files and live job prompts are INDEPENDENT — update both.

## Build slices

### Phase 1 — review loop, dry-run (no merge authority)  ✅ DONE, tests green
- [x] `docker/profiles/auditor/SOUL.md` — persona + review rubric + decision policy
      + risk-tier globs.  ← review rubric awaiting Brais's edits.
- [x] `auditor/__init__.py`, `auditor/pending.py` — open PRs unreviewed at head SHA;
      `$HERMES_HOME/auditor/state.json` dedup (mirrors incidents/state.json).
- [x] `auditor/tiers.py` — classify changed files `system` | `content` (fail-safe).
- [x] `auditor/auditor.prompt` — dry-run cron prompt (review + comment, NO merge).
- [x] Tests: `tests/auditor/test_pending.py` + `test_tiers.py` — 15 passing.

### Phase 2 — auth + live wiring
Bot account `hermes-auditor` created + Write collaborator on hermes-sandbox ✓ (2026-06-24).
CODE DONE (this branch), needs deploy + runtime steps:
- [x] `docker/profiles/auditor/repos.txt` — clones braisntext/hermes-sandbox into workspace.
- [x] `docker/profiles/auditor/routing.env` — escalations → incidents thread 1904.
- [x] `03-biglobster-config §1b` — auditor `.env` GITHUB_TOKEN/GH_TOKEN overridden from
      `HERMES_AUDITOR_GITHUB_TOKEN` (resolves env → main .env). Invariant: auditor holds
      ONLY the bot token. Verified: bash -n + py_compile clean.
- [x] `03-biglobster-config §4b` — auditor `home/.git-credentials` + commit identity
      (`hermes-auditor`) pinned to the bot token; never the shared one.
- [x] `auditor/llm.py` — LLM-as-judge: resolves review model per tier from env
      (`HERMES_AUDITOR_SYSTEM_MODEL` / `HERMES_AUDITOR_CONTENT_MODEL`), OpenRouter call,
      cheap fail-safe defaults. `--show-model` to verify env took effect. +6 tests.
- [x] `03-biglobster-config §1c` — stamp those two model vars into auditor `.env` so the
      cron agent inherits them (Zeabur env → .env). Auditor-only, not in shared inject.
- [x] `auditor.prompt` + SOUL — use `auditor/llm.py --tier <tier>` for the review.
- [ ] RUNTIME (on container, post-deploy): `hermes profile create auditor`; confirm
      `HERMES_AUDITOR_GITHUB_TOKEN` reachable by cont-init; `hermes cron create` agent job
      (profile=auditor, workdir=.../workspace/hermes-sandbox, ~*/10min, prompt=auditor.prompt).
- [ ] DEPLOY GATE: commit + push + deploy (Cloud Build→ghcr→Zeabur) — needs Brais's OK.
- [ ] Adopt branch+PR workflow: Claude Code (me) + Telegram Hermes prompt.

### Phase 3 — grant merge + enforce
- [ ] Flip merge authority on after clean dry-runs (gh pr merge on approve).
- [ ] git-guard `pre-push` layer: block agent identities pushing directly to `main`
      (makes the gate enforced, not just conventional).

### Phase 4 (later) — extend to profile repos + dead-cron handling
TOPOLOGY: each profile owns its OWN GitHub repo, not a path in hermes-sandbox.
Repo set = union of `docker/profiles/<name>/repos.txt` + `hermes-sandbox`:
biglobster, grow-shop-api, grow-shop-landing, FinView, SocialAgenda (+ engine).
- [ ] Repo list: aggregate the `repos.txt` files (+ hermes-sandbox); cron loops
      `auditor/pending.py --repo <slug>` per repo (pending.py already repo-agnostic).
- [ ] Per-repo risk tiers: `tiers.py` is hermes-tuned only. Add tier profiles keyed
      by repo slug (biglobster Node site, FinView, grow-shop split, etc.) — without
      them, profile-repo content PRs over-review on the strong model.
- [ ] Dead-cron handling: low-risk dead-cron PRs → patch-and-merge; high-risk →
      escalate to human (live agents already converse via PR comments).
- [ ] PAT scope: `hermes-auditor` needs write on every gated repo (see Blocked).

## Blocked on Brais
- Create GitHub bot account `hermes-auditor` + fine-grained PAT (repo write on the
  hermes repo). Everything else builds against a placeholder `HERMES_AUDITOR_GITHUB_TOKEN`.

## Verification
- Phase 1: unit tests green; run `auditor/pending.py` against a throwaway PR; confirm
  it posts a sensible review comment in dry-run.
- Phase 3: confirm a deliberately bad PR is blocked + escalated; a clean PR is merged.
