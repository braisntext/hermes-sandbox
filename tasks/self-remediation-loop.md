# Self-Remediation Loop — implementation plan

Close the Decide→Act link in the autonomy loop: let the incident watcher
*fix* known reversible failure classes, not only alert. Governed by a hybrid
ledger (track record recommends promotion; CEO approves). Builds on the
existing 60m incident watcher (`f0d670b8e3b7`) and its silence/heartbeat
contract.

## Locked decisions
- **First loop:** self-remediation of incidents (Tier-0, reversible only).
- **Trust model:** hybrid — ledger records track record + *recommends*
  promotion; CEO approves each tier bump. No global capability flags.
- **Lifecycle per class:** `gated` → (K=5 clean runs) → `auto`.
  - `gated`: detect → propose → **CEO approves EACH execution** (hand-gated).
  - `auto`: detect → execute bounded fix → next-tick self-verify.
- **Promotion threshold:** K = 5 clean, hand-approved-and-verified runs, zero rollbacks.
- **Verify mechanism:** next watcher tick (60m) — signature cleared = success,
  still present = failed → escalate to thread 1904.
- **Seed classes:**
  - `model-fallback` → seeded as `auto` (already live in prod, PR #51; models reality).
  - `cron-transient-failure` → retry-with-backoff. Starts `gated`.
  - `shared-clone-branch-confusion` → reset-to-main + re-pin `*@agent.local` identity
    (the SEO/Gap-Hunter hazard, known fix recipe). Starts `gated`.

## Non-negotiable P0 guards (build FIRST, before any auto-act)
- **Per-signature debounce:** ledger check "already acted on this signature
  within window" → kills retry storms / double-action race on the 60m loop.
- **Global kill switch:** watcher reads `HERMES_AUTONOMY` first; `paused` freezes
  all auto-acts (gated proposals still post). One move to stop everything.
- **Rate limit:** max N auto-acts per class per window; exceed → escalate, don't act.
- **Reversibility invariant:** a class cannot enter the registry without a declared
  reversal. No reversal = not eligible for `auto`, ever.

## Architecture
- **Registry** (repo-managed, deploys via §6 clone pull): class definitions —
  `signature matcher`, `bounded fix`, `reversal`, declared tier. Code, version-controlled.
- **Mode state** (volume, runtime — like `gateway_state`): per-class `gated`/`auto`,
  survives reboot, NOT clobbered by §6d resync.
- **Ledger** (volume, append-only JSONL — mirrors `incidents/blocked-commits.jsonl`):
  `ts, class, job/repo, signature, mode, action, outcome, reverted`.
- **Watcher extension:** on matching a registered class →
  `auto` ⇒ debounce-check → execute fix → log (pending) → next tick verifies;
  `gated` ⇒ write `pending` ledger row + post actionable proposal to 1904.
- **Approval CLI** (`gated` execution gate): `hermes remediate list` /
  `hermes remediate apply <id>` — runs the registered bounded fix, records outcome.
  CEO is the trigger; system owns the action definition + logging. No Telegram
  reply-consumer needed (the no_agent watcher can't consume replies).
- **Promotion recommender:** counts clean runs per class; at ≥K emits one line to
  1904 — "promote `<class>` to auto? (K/K clean)". CEO approves → flip mode state.

## Build slices

### Phase 0 — guards + ledger (foundation)  ✅ DONE, 15 tests green
- [x] Ledger module (`remediation/ledger.py`): append-only JSONL writer/reader,
      per-signature debounce query (`recently_acted`), per-class rate count (`act_count`),
      cap+prune. Mirrors `incidents/blocked-commits.jsonl` conventions.
- [x] `HERMES_AUTONOMY` kill-switch read (`remediation/guards.py:autonomy_paused`).
- [x] Per-class rate limiter (`RATE_MAX_PER_CLASS`, folded into `may_auto_act`).
- [x] `may_auto_act()` — single gate Phase 3 must pass (killswitch→debounce→ratelimit).
- [x] Unit tests (`tests/test_remediation_guards.py`): ledger roundtrip + malformed
      tolerance + prune, debounce window in/out, rate-limit trip, kill-switch freeze,
      combined-gate precedence. 15 passed.
- NOTE: kill-switch read lives in the guard module (pure, testable); wiring it into
  the watcher entrypoint happens in Phase 1 when the watcher first calls the gate.

### Phase 1 — registry + gated proposals (no auto-act yet)  ✅ DONE, 39 new tests green
- [x] Registry (`remediation/registry.py`): `RemediationClass`, transient-vs-hard-fault
      heuristic, `cron-transient-failure` (gated, fix=`cron.jobs.trigger_job`), `classify()`.
      Signature = the incident id the watcher already mints (no new scheme).
- [x] Mode state (`remediation/modes.py`): `modes.json` on volume, `mode_for`/`is_auto`/
      `promote`/`demote`, fails safe to registry default (never silently auto). §6d-safe.
- [x] Watcher enrichment (`incidents/sweep.py:_remediation_hint`): classifiable incidents
      get a "Proposed remediation … apply <id>" line in the brief. Disk-free, lazy import,
      silence contract preserved. (No ledger write from the watcher — CLI owns that.)
- [x] Standalone CLI (`python -m remediation.cli list|apply <signature>`): `list` =
      live incidents that classify, debounce-filtered; `apply` re-validates LIVE
      (no retry of a recovered job), runs the bounded fix, logs `applied`+outcome.
- [x] Tests: transient heuristic precision (hard-fault veto), mode fail-safe, brief
      enrichment, apply→ledger, re-validate-live, debounce-one-per-occurrence.
- DECISIONS (CEO-approved this session):
  - Tier-0 admission relaxed to **reversible OR bounded-idempotent** (a retry has no
    undo but debounce caps it to one per occurrence; `reversal=None` + rationale).
  - `model-fallback` is **documented precedent, NOT a registry entry** (it self-heals
    in the delegate lane before the watcher sees it — no detect→fix to model).
  - CLI is **standalone** (`python -m remediation.cli`), not a `cli.py` subcommand.
  - `shared-clone-branch-confusion` **DEFERRED to its own slice**: its fix runs
    `git reset --hard` in a shared clone = the 2026-06-22 cover-wipe hazard; needs the
    merge-time mass-deletion safety net + careful design before it joins the registry.

### Phase 2 — verify + promotion recommender  ✅ DONE, 18 new tests; 72 total green
- [x] Next-tick verification (`remediation/reconcile.py:verify_pending`): success =
      **job re-ran since the apply AND is healthy** (not mere signature rotation);
      still-failing ⇒ `failed` + escalation; not-yet-rerun ⇒ stays pending.
- [x] Clean-run counter (`clean_run_count`): distinct gated verified-success signatures.
- [x] Promotion recommender (`promotion_recommendations`): ≥K=5 ⇒ "promote?" to 1904,
      deduped via a `recommended` ledger event (24h cooldown), skips already-auto.
- [x] `reconcile()` orchestrates verify→recommend in one pass (a just-verified run can
      tip the threshold same tick); wired into `sweep.py` (escalations/recs are
      substantive output + reset heartbeat; clean pass stays silent; dry-run-safe).
- [x] Promotion approval CLI: `python -m remediation.cli promote|demote <class>` —
      `promote` flips gated→auto + logs `promoted`; `demote` is the instant brake.
- [x] Tests: verify success/fail/pending/absent-job, no-re-verify, clean-run count,
      recommend at-K + dedup + already-auto, reconcile e2e, sweep integration, CLI.
- DESIGN NOTE: verification keys on JOB HEALTH, not signature rotation — a retried
  job that immediately re-fails (even for a new reason) does NOT count toward
  promotion. Conservative: the track record only rewards fixes that restored health.

### Phase 3 — first auto-act (only after a class is promoted)  ☐
- [ ] `auto` path executes bounded fix under debounce + rate-limit + kill-switch.
- [ ] Dry-run validate on `model-fallback` (already proven) before any new class flips.

## Deploy notes
- Registry/ledger/watcher code = §6 clone pull (restart, not rebuild).
- Mode state = volume runtime (never hand-edit; promotion is the only writer).
- If watcher entrypoint is image-baked, kill-switch wiring may need ONE rebuild —
  confirm at build time.
- Silence contract: gated proposals + escalations + promotion asks go to 1904;
  clean ticks stay `[SILENT]`; 24h heartbeat unchanged.

## Verification gate (before calling done)
- [ ] Retry storm impossible: debounce proven with a forced repeat signature.
- [ ] Kill switch freezes auto-acts mid-flight (test with a promoted class).
- [ ] A gated class cannot execute without `remediate apply`.
- [ ] Ledger is the single source of truth for "what acted, when, outcome".
- [ ] Promotion requires CEO approval — no auto-promotion path exists.
