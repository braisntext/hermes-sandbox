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

### Phase 1 — registry + gated proposals (no auto-act yet)  ☐
- [ ] Registry with the 3 seed classes; `model-fallback`=auto, others=gated.
- [ ] Mode state on volume + load/persist (reboot-safe, §6d-safe).
- [ ] Watcher: detect registered class → write pending row + post proposal to 1904.
- [ ] `hermes remediate list` / `apply <id>` CLI (executes bounded fix, logs outcome).
- [ ] Tests: matcher precision, proposal format, apply→outcome recording.

### Phase 2 — verify + promotion recommender  ☐
- [ ] Next-tick verification: signature cleared ⇒ success; else escalate.
- [ ] Clean-run counter per class; ≥K ⇒ promotion recommendation to 1904.
- [ ] Promotion approval flips mode state gated→auto.
- [ ] Tests: verify success/fail paths, promotion trigger at K, mode flip persists.

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
