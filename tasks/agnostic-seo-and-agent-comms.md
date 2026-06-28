# Project-Agnostic SEO + Inter-Agent Comms — Design Note

**Goal:** expand the onsite-SEO cron in 3 directions (awareness, capabilities,
inter-agent comms), build + test on the **biglobster** profile, but design every
artifact **profile-parameterized from day one** so any current/future project
(finview, grow-shop, …) can activate it as *data, not code*.

**Core principle:** the agent is a generic engine; everything project-specific is
**profile config read at run start**. Nothing hardcodes "biglobster". Hermes
profiles ARE the multi-tenancy primitive — "project-agnostic" == "profile-keyed".

---

## Existing patterns to reuse (don't reinvent)

- `offsite-geo/geo-scout.prompt` — cron shape: CONFIG block, HARD GUARDRAILS,
  append-only `ledger.md` in job workdir, "single bounded run, NOT a loop",
  draft-only + human/auditor gate. **Trap:** its CONFIG block lives *in the
  prompt* → move per-project values OUT to profile config (see Trap 1).
- `remediation/` (`ledger.py`, `modes.py`, `registry.py`, `guards.py`,
  `clone_safety.py`) — the proven gated→auto trust + coordination template.
  Reuse for the mailbox and Editor-in-Chief.
- Auditor PR gate — every agent action terminates in a PR → auditor reviews on
  GitHub. The mailbox proposes; the auditor disposes. No agent self-merges.

---

## Part 1 — Awareness (site-state)

Persistent `site-state.json` per profile, refreshed at top of every run from a
live crawl + GSC pull. Holds: page inventory (URL, target keyword, word count,
last-touched, schema y/n), internal-link graph, keyword→page map (cannibalization),
per-page GSC metrics (impressions, clicks, avg position, CTR). **The run-to-run
diff IS the work queue** — this alone ends idle runs.

Strategies it unlocks: short (striking-distance pos 5-20 push, CTR rescue, orphan
links), medium (cannibalization, content-decay refresh, topic clusters), long
(topical-authority map, seasonality).

## Part 2 — Capabilities (broaden the verb set)

Beyond copy-rewrite, low-risk mechanical surface on static HTML: JSON-LD schema,
FAQ-from-GSC-queries, image SEO (alt/filename/WebP), internal-link automation,
OG/Twitter cards, broken-link sweep, canonical/dedup, sitemap/robots upkeep,
heading/a11y, CWV hints. Risk-tiered to the auditor (content = auto-merge,
system = advisory). Guardrail: ONE bounded improvement per run, highest-signal
item from site-state, then stop. Per-project `enabled_capabilities` list.

## Part 3 — Inter-Agent Comms (mailbox + orchestrator)

Cross-agent work requests (SEO finds thin content → content agent expands; 2 pages
cannibalize → merge + canonical). Substrate: **GitHub Issues as message bus** —
durable, CEO-visible, lives OUTSIDE the shared clone (dodges cover-wipe hazard).

- **Feature, all agents (first):** `agent_mailbox` helper — open/read/close
  **profile-namespaced** labeled issues (`<profile>/from:seo`, `<profile>/agent:content`).
- **New cron + chip (second, after mailbox exists):** Editor-in-Chief triage agent
  — reads mailbox, prioritizes, routes, closes stale. Own ledger.

---

## Engine vs. per-project config

| Concern | Shared engine | Per-project (profile config) |
|---|---|---|
| SEO logic | crawl, GSC pull, strategy select, PR | site_url, gsc_property, brand/voice, language, repo, telegram_topic |
| site-state | schema + diff/queue logic | storage path `<profile>/seo/site-state.json` |
| capabilities | the 10 actions + risk tiers | `enabled_capabilities: [...]` |
| mailbox | helper + loop guards | labels namespaced per profile |
| gated→auto trust | `remediation/modes.py` | trust keyed on **(profile × handoff_type)** |

**Activation = data:** drop `profiles/<project>/seo.config.yaml` + register cron
`--profile <x>`. No prompt fork, no code change.

---

## Three agnosticism traps (design against now)

1. **Config-in-prompt drift** — live `jobs.json` prompt and repo `.prompt` are
   independent. Per-project values in the prompt multiply that drift across N
   projects. Push them to profile config read at run start (one canonical source).
2. **Cross-profile state bleed** — mailbox labels, ledgers, site-state, locks all
   MUST be profile-scoped. Unscoped label = biglobster agent files work against finview.
3. **Trust isn't transferable** — gated→auto promotion must NOT be global. `auto`
   on biglobster restarts as `gated` on every new project. Key on (profile, handoff_type).

## Systems flags (the loop is the dangerous part)

- **State location:** mailbox + site-state live OUTSIDE `/opt/data/<profile>` shared
  clone (branch-confusion / cover-wipe hazard). GitHub Issues or separate volume path.
- **Blast radius / loops:** A→B→A infinite-token loop. Mandatory: max-hop counter,
  content-hash dedupe, request TTL. Reuse remediation guards.
- **Races:** crons async; two agents editing same page = clobber. Page-level lock
  label (claimed-by) + keep the mass-deletion tripwire on every clone.
- **Gate stays:** all paths end in PR → auditor.

---

## Build order

Every artifact takes a `profile` param from day one (profile-first, not retrofit).

1. **Slice 1 (DONE, merged #89): site-LEVEL awareness aggregate.** Internal-link
   graph + orphans via cron-safe helper. Verified trace c21a7866.
2. **Slice 2 (DONE, this branch): Open Graph + CWV capabilities** — the only two
   Table-2 items not already in the live actions. Prompt-only (no helper): Acción 10
   (OG/Twitter meta, full auto-inject from page content) + Acción 11 (CWV, mostly
   advisory — auto only lazy-load below-fold imgs + eager/fetchpriority hero; alert
   for JS/CSS load-order). Splice doc: `seo-og-cwv-prompt-patch.md`.
3. `agent_mailbox` feature (profile-namespaced), reads site-state.
4. Editor-in-Chief triage cron (new chip) — only after mailbox has emitters.
5. Agnostic infra: install build_sitestate.py via cont-init to a shared path.

---

## CORRECTION after reading the live prompt (2026-06-28)

Read the actual biglobster onsite-SEO prompt. Major reframes:

- **JSON-LD already ships — it's Action 6** (validate + inject Article/Organization/
  BreadcrumbList/FAQPage, with a syntax gate + idempotency). The earlier "Slice 1 =
  JSON-LD" was rebuilding existing work. **Scrapped.**
- **"Agent does nothing lately" is correct behavior, by design.** The maturity model
  (CATCH-UP → PROPAGACIÓN → MANTENIMIENTO) explicitly says *"la ausencia de trabajo
  es un resultado válido"* — a 0-URL batch in MANTENIMIENTO is the system working.
  The fix is NOT more verbs on the same pages; it's better SIGNAL (awareness).
- **9 of my 11 Table-2 capabilities already exist** as Actions 1–9. Only genuinely
  missing: Open Graph/social meta, Core Web Vitals hints.
- **Awareness is half-built:** a per-URL ledger exists at `/opt/data/seo/seo-ledger.json`.
  Missing = the site-LEVEL layer (link graph, persisted keyword→URL map). That
  aggregate is the real Table-1 gap and the prerequisite for Table-3 merge handoffs.

### Two defects found in the live prompt
1. **Batch-size contradiction:** PRE-FLIGHT says *"hasta 1 URLs del día"* then
   *"Selecciona las 5 URLs con mayor score."* Conflicting. **OPEN: confirm intended
   value** (5 = apparent design intent; 1 may be a deliberate throttle someone set).
2. **Ledger path is global, not per-profile:** `/opt/data/seo/seo-ledger.json` —
   finview/grow-shop would collide on the same file. Breaks the agnostic goal.

### Decisions (revising last turn, new evidence)
- **State location = volume, per-profile** (reverses the earlier committed-repo-file
  choice). Matches the existing volume-ledger convention; no per-run state-diff
  commit (which clashed with the agent's one-atomic-commit-per-URL discipline).
- Path must stay OUTSIDE the clone: `/opt/data/profiles/<profile>/seo/` (biglobster:
  `/opt/data/profiles/biglobster/seo/`). NOT `/opt/data/<profile>/` — that IS the
  clone (`/opt/data/biglobster`) and would re-arm the cover-wipe hazard.
- **Fold both defects into this slice** (per-profile path is already in scope).

## Slice 1 spec — site-LEVEL awareness aggregate (prompt-patch)

Prompt-driven agent, no code; deliverable is a patch to the live `jobs.json` prompt.

**New `site-state.json`** (sibling of the ledger, per-profile volume path), rebuilt
read-only at run start after PASO 0:
- `internal_link_graph`: per-URL `{outbound, inbound}`, from parsing the repo's
  already-checked-out `web/blog/*.html` + `web/*.html` with a real HTML parser.
- `keyword_url_map`: query→[urls] from one GSC pull, **persisted** so cannibalization
  is a cross-run signal, not re-derived live each run.
- `orphans`: URLs with empty `inbound`. `cannibalization`: queries with ≥2 URLs.
- `built_against_commit`: main SHA the graph was built against.

**Wiring (feeds existing actions, replaces nothing):**
- Delta trigger #3 (orphans) + `Flag_canibalización` in scoring read from here,
  deterministically, instead of live re-derivation.
- Action 1 prioritizes linking `orphans` → pillar. Action 4 starts from computed
  `cannibalization`. Future inter-agent merge handoffs read this file.

**Prompt edits folded in:**
- Move ledger path `/opt/data/seo/` → `/opt/data/profiles/<profile>/seo/` (3 spots).
  ⚠️ **Migration gotcha:** copy the existing ledger to the new path once, or the
  agent reads baseline_complete=false for everything and triggers a full CATCH-UP
  re-sweep. One-time op before the patched prompt goes live.
- Define a single `BATCH_SIZE` param up top, remove the 1-vs-5 contradiction
  (value pending CEO confirmation).

**Guardrails:** HTML parsed with a real parser (not regex); partial site-state on
parse failure beats none; site-state is READ-ONLY for the day's decisions (observe,
don't "optimize" it); never write it inside `/opt/data/biglobster`.

## Repo-hygiene gap (DONE)
The onsite-SEO prompt is now checked in as `onsite-seo/seo-agent.prompt` (was only in
`jobs.json` on the volume). Repo-vs-live independence gotcha still applies: edits need
to land in BOTH.

## First live run — trace b1d30a47 (2026-06-28): slice BLOCKED, root-caused, fixed

Pulled the run via Langfuse. Awareness slice did NOT land first try; trace resolved
the prompt-driven-vs-helper fork with evidence:

- **Cron sandbox blocks inline code.** `execute_code` → BLOCKED ("arbitrary local
  Python, no approver"); `python3 -c` → BLOCKED ("dangerous -e/-c flag"). The agent
  couldn't build the graph inline; `site-state.json` was never written. It degraded to
  grep and still found 2 real orphans (`sostenibilidad-esg`, `caso-exitoso-quickpay`).
- **Dir perms:** `/opt/data/profiles/biglobster/seo/` → `Permission denied` (root-owned).
- **Mode → CATCH-UP** because the ledger was unreadable (perms / none yet).
- **Hit iteration cap (90 LLM calls) mid-task**, no Telegram report; much budget burned
  retrying the blocked Python.
- **NOT credits:** 90 calls, ~76.6k in / 13.7k out tokens, 0 model errors.

### Fork RESOLVED → deterministic committed helper
`onsite-seo/build_sitestate.py` (stdlib-only, arg-driven, atomic write, `--merge` to
keep GSC fields). Agent invokes it BY PATH via `terminal` → normal file execution,
clears the approval gate; versioned + auditable. Prompt `[ESTADO DEL SITIO]` now calls
it. Smoke-tested locally (5 pages → 1 orphan, inbound correct). Deploy + `chown` in
`seo-awareness-prompt-patch.md`. Helper builds the file-derived part; keyword_url_map/
cannibalization stay agent/GSC-populated.

**Secondary follow-up (not blocking):** if the run still overruns 90 iterations once
unblocked, bump `HERMES_MAX_ITERATIONS`. Agnostic follow-up: install the helper via
cont-init to a shared path instead of per-profile hand-copy.

### VERIFIED LANDED — trace c21a7866 (2026-06-28, run #2)
Slice works end to end after deploying the helper + `chown hermes:hermes`:
- `build_sitestate.py` ran via `terminal` → `exit_code:0`, `pages=66 orphans=4
  commit=bc0cd771…`. NO BLOCKED error. (commit populated = ran as hermes, not root.)
- Mode = **MANTENIMIENTO** (ledger readable now; CATCH-UP fallback gone).
- Run **completed with the Telegram report** in ~3 min / 45 obs (was ~12 min / 201).
  No iteration-cap overrun — reclaimed budget as predicted.
- Awareness is live: report Insights cite "el grafo interno muestra 4 huérfanos
  persistentes". Agent correctly did nothing (steady state) — observable, explained.

Refinements applied this round:
- Orphan false positives excluded via `--pillar`: `/404.html` and
  `/blog/infografias/canonical-infographic-template.html` (real orphans kept:
  caso-exitoso-quickpay, sostenibilidad-esg-pymes-espana-2026).
- Prompt now steers ledger reads/date-math to read_file/write_file/terminal (the
  agent still reached for execute_code once for ledger math → blocked, harmless).

Still open: re-apply the updated `[ESTADO DEL SITIO]` + ledger note + 2 new --pillar
lines to the live `jobs.json` (repo is now ahead of live again).
