# Hermes Architect Agent — BigLobster Integration

Hermes runs as a service on Zeabur (same project as BigLobster) and communicates over the internal network. The COO delegates long-running tasks to it asynchronously.

## How it works

```
BigLobster COO (Zeabur)
  → POST http://hermes-sandbox.zeabur.internal:9119/api/delegate
                               └─ runs agent in background thread
  ← POST https://biglobster.top/api/hermes-callback  (async, when done)
BigLobster sentinel.js
  → notifyCeo() → Telegram message to CEO
```

---

## Infrastructure

| Component | Value |
|-----------|-------|
| Service name | `hermes` (Zeabur, same project as BigLobster) |
| Internal URL | `http://hermes-sandbox.zeabur.internal:9119` |
| Public URL | `https://blhermes.zeabur.app` |
| Repo | `github.com/braisntext/hermes-sandbox` |
| Docker image | `ghcr.io/braisntext/hermes-sandbox:latest` |
| Deploy method | **Docker image from GHCR** (not git autodeploy — see Deploy section) |

---

## Deploy Process

⚠️ Hermes does NOT autodeploy from git push. Cloud Build builds the image and pushes to GHCR; Zeabur then requires a manual restart to pull it.

### Why
Zeabur's build timeout is too short for the 2.7GB image (Playwright + Chromium + Python + Node). GitHub Actions is not enabled on this repo. Cloud Build runs on GCP with no timeout issues.

### Current flow

1. Merge changes to `main`
2. Trigger Cloud Build from the repo root:
   ```bash
   cd /Users/brais/VSCODE/hermes-sandbox
   gcloud builds submit --config=cloudbuild.yaml --substitutions=_GITHUB_TOKEN="<ghcr_pat>"
   ```
   Cloud Build builds the image and pushes it to `ghcr.io/braisntext/hermes-sandbox:latest`.
3. Restart Hermes service in Zeabur dashboard

### Config-only changes (no rebuild needed)

For model changes or env var updates, use Zeabur Variables instead:
- `HERMES_DEFAULT_MODEL` — overrides model in config.yaml on every boot
- Takes effect on next Zeabur restart (no image rebuild needed)

---

## Zeabur environment variables (Hermes service)

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENROUTER_API_KEY` | Yes | OpenRouter API key |
| `HERMES_CALLBACK_SECRET` | Yes | Shared secret for callback auth. Must match BigLobster's value. |
| `HERMES_CALLBACK_URL` | Yes | `https://biglobster.top/api/hermes-callback` |
| `HERMES_DEFAULT_MODEL` | Yes | Active model. Currently: `deepseek/deepseek-v4-flash` |
| `HERMES_MAX_ITERATIONS` | No | Max agent turns per task (default: 60) |
| `HERMES_DASHBOARD` | Yes | Set to `1` to start the web dashboard on port 9119 |
| `TELEGRAM_BOT_TOKEN` | No | Token for direct CEO ↔ Hermes Telegram chat (@b_l_hermes_bot) |
| `TELEGRAM_ALLOWED_USERS` | No | CEO's Telegram user ID for direct access |
| `EXA_API_KEY` | No | Exa web search API key — activates the `web` toolset |
| `HUGGINGFACE_API_KEY` | No | HuggingFace token — activates `video_gen` (text-to-video) |

## Zeabur environment variables (BigLobster service)

| Variable | Value |
|----------|-------|
| `HERMES_URL` | `http://hermes-sandbox.zeabur.internal:9119` |
| `HERMES_CALLBACK_SECRET` | Same value as above |

---

## Model

`deepseek/deepseek-v4-flash` via OpenRouter.

Set via `HERMES_DEFAULT_MODEL` env var in Zeabur — the `03-biglobster-config` boot hook updates `config.yaml` on the persistent volume at every boot. The `docker/config.yaml` in the repo also reflects this as the default.

### Why not owl-alpha
`openrouter/owl-alpha` was the original model but became unstable (consistent "Provider returned error" on OpenRouter). Switched 2026-05-21.

### Why not tencent/hy3-preview
In Hermes, the `tencent/` prefix resolves to the `tencent-tokenhub` provider (TokenHub API), not OpenRouter. Using it as the default causes a startup crash because `TOKENHUB_API_KEY` is not set.

---

## Direct CEO ↔ Hermes Telegram

The CEO can talk directly to Hermes without going through BigLobster/COO:
- Bot: `@b_l_hermes_bot`
- Configured via `TELEGRAM_BOT_TOKEN` + `TELEGRAM_ALLOWED_USERS` in Zeabur
- After adding/changing these vars, click **Restart Gateway** in the Hermes web panel (not just Zeabur restart)

---

## Gateway

The Hermes gateway handles Telegram messages and BigLobster delegation. It starts **automatically** on every container boot — no manual step needed.

**Boot sequence (s6-overlay, since the 2026-06 upstream upgrade):**
1. `/init` (PID 1) brings up the s6 supervision tree and runs the `cont-init.d` hooks:
   `01-hermes-setup` (UID remap, volume chown, config/SOUL seed, skills sync) →
   `015-supervise-perms` → `02-reconcile-profiles` (recreates per-profile gateway s6
   slots from the volume and **auto-starts any whose last `gateway_state.json` was
   `running`** — this is what restarts the gateway across restarts) →
   `03-biglobster-config` (env sync, config key enforcement, MEMORY seed, egress check).
2. s6 then starts the supervised `dashboard` service (when `HERMES_DASHBOARD=1`) on port 9119
   and the (no-op) `main-hermes` slot; the container's `CMD` (`sleep infinity`) runs as the
   s6 "main program" so the container stays alive while the dashboard + gateways serve.

The pre-s6 entrypoint that polled `/health` then ran `hermes gateway restart` is gone; the
gateway is now restored natively by `container_boot.py` (cont-init `02`) from the persisted
`gateway_state.json` on the volume.

**To manually restart the gateway** (e.g. after changing Telegram env vars):
1. Go to `https://blhermes.zeabur.app`
2. Click **Restart Gateway** in the System section (bottom left)

Check status: Gateway Status should show **RUNNING** in the bottom-left of the panel.

---

## Mounted workspaces

The Hermes container's only writable volume is `/opt/data/` (Zeabur persistent volume, `HERMES_HOME=/opt/data`). The `stage2-hook.sh` cont-init step pre-creates `/opt/data/workspace/` on boot.

> ⚠️ `/workspace/` does **not** exist at the container root. All paths must be under `/opt/data/`.

| Container path | Purpose |
|----------------|---------|
| `/opt/data/workspace/FinView` | FinView repo working directory |
| `/opt/data/workspace/WorldHawk` | WorldHawk repo working directory |
| `/opt/data/workspace/grow-shop` | Grow Shop repo working directory |
| `/opt/data/workspace/bl-site-package` | bl-site-package repo working directory |
| `/opt/data/workspace/biglobster` | BigLobster repo working directory |
| `/opt/data/biglobster/` | BigLobster output files (images, reports, exports) |
| `/opt/data/cache/images/` | Auto-saved images from `image_generate` tool |

---

## Available tools

Hermes activates tools based on configured API keys. The `03-biglobster-config` boot hook forces provider settings into `config.yaml` on every boot.

| Toolset | Provider | Key required | Notes |
|---------|----------|-------------|-------|
| `web` | Exa | `EXA_API_KEY` | Web search + content extraction |
| `image_gen` | OpenRouter | `OPENROUTER_API_KEY` | FLUX.2-klein-4b via `openrouter.ai/api/v1/images/generations` |
| `video_gen` | HuggingFace | `HUGGINGFACE_API_KEY` | Text-to-video via `router.huggingface.co` (damo-vilab/text-to-video-ms-1.7b) |

Plugins live in `plugins/image_gen/` and `plugins/video_gen/` — **baked into the Docker image**, not synced at boot. Changes require a rebuild.

---

## Skills

Reusable Hermes skills live in `/opt/data/skills/` on the persistent volume.

| Skill | Path | Description |
|-------|------|-------------|
| `prospeccion-local` | `research/prospeccion-local/SKILL.md` | Geographic lead prospecting for BigLobster ICP. Searches local businesses by zone, extracts contacts, filters Ourense city, POSTs to `/api/hermes-leads`. Usage: "Prospecta el polígono de San Cibrao das Viñas" |

---

## Session persistence for delegated tasks

Tasks arriving via `/api/delegate` (from BigLobster COO) are now persisted to `state.db` so they are queryable in future Hermes sessions via `session_search`.

**How it works:**
- `_run_delegate_agent` (`hermes_cli/web_server.py`) instantiates `SessionDB()` and passes it to `AIAgent` as `session_db`
- Session is created in `state.db` with `session_id = task_id` (e.g. `hermes-1779505977261`) and `source = "api"`
- All messages (prompt, tool calls, results) are flushed to `state.db` on task completion
- The session is searchable via `session_search("hermes-1779505977261")` or by topic keywords in any future Hermes session

**Note:** This requires a Docker image rebuild to reach production. Sessions created before the fix (commit `ef1b6922`) are not in `state.db` and cannot be recalled.

**On the BigLobster side:** Each delegated task is also recorded in BigLobster's `agent_tasks` SQLite table (`request_id = task_id`), updated to `completed`/`failed` when the callback arrives. Query:
```sql
SELECT * FROM agent_tasks WHERE request_id = 'hermes-1779505977261';
```

---

## Smoke test

```bash
# Container health probe (always returns 200 if process is alive)
curl https://blhermes.zeabur.app/health

# Richer status (gateway state, active sessions, model)
curl https://blhermes.zeabur.app/api/status

# Manual delegate (from BigLobster or any service in same Zeabur project)
curl -X POST http://hermes-sandbox.zeabur.internal:9119/api/delegate \
  -H "Content-Type: application/json" \
  -d '{
    "task_id": "test-001",
    "prompt": "List files in /opt/data/workspace/biglobster and return a summary.",
    "webhook_url": "https://biglobster.top/api/hermes-callback"
  }'
# Expected: {"task_id":"test-001","status":"accepted"}
```

---

## Startup log verification

After every Zeabur restart, confirm these lines appear in the logs (s6-overlay format
since the 2026-06 upgrade — the old `[entrypoint] …` lines are gone):

```
[03-biglobster] Synced env vars into /opt/data/.env
[03-biglobster] config.yaml keys already current   (or "Reconciled config.yaml keys")
[03-biglobster] Egress openrouter.ai: 200
[03-biglobster] Egress router.huggingface.co: 200   (or FAIL if Zeabur blocks it)
[startup] Model: deepseek/deepseek-v4-flash | Provider: openrouter | API key present: True
```

The native gateway reconciler (cont-init `02`) logs one line per profile, e.g.
`reconcile … default … started` (or `registered` if its last state wasn't `running`).
**The gateway should come up automatically** because the volume's `gateway_state.json`
already records `running`. If — only on the very first upgrade boot — it comes up
`registered`/down instead, click **Restart Gateway** once in the web panel; it self-heals
on every subsequent restart.

If `API key present: False` or `Provider resolution FAILED` → check `OPENROUTER_API_KEY` in Zeabur Variables.

---

## Logs

Zeabur → hermes service → **Logs** tab.

For agent execution detail: `https://blhermes.zeabur.app/logs`

---

## Incident log

### 2026-06-02 — Upstream upgrade (v0.13.0 → v2026.5.29.x) + tini→s6-overlay

Merged ~1,980 upstream commits (`hermes-upstream-upgrade` branch) and folded in the
profile-scoped `/api/delegate` work (1a). Key changes affecting ops:

1. **tini → s6-overlay.** The container ENTRYPOINT is now `/init`; the old
   `docker/entrypoint.sh` is a deprecated upstream shim. Bootstrap (UID remap, chown,
   config/SOUL seed, skills sync) runs in `docker/stage2-hook.sh` via cont-init `01`.
2. **Our env-patching re-homed** out of `entrypoint.sh` into a new additive
   `docker/cont-init.d/03-biglobster-config` (env sync, config key enforcement, MEMORY
   seed, egress check). Logs now prefixed `[03-biglobster]`, not `[entrypoint]`.
3. **Gateway autostart is now native** (`container_boot.py`, cont-init `02`): restored from
   the volume's `gateway_state.json` instead of an entrypoint `/health`-poll loop.
4. **Dashboard is a supervised s6 service**; `config.yaml` first-boot seed source switched to
   `docker/config.yaml`; `/api/delegate` moved into the shared `dashboard_auth/public_paths.py`
   allowlist (covers both the session-token and OAuth gates).
5. **Config schema migrated v23 → v25** automatically on boot (non-destructive, additive).

Verified pre-deploy: 476 tests green (image_gen, web_server/delegate/callback-auth/host-header,
container_boot) + config-migration smoke. Deploy = same flow below (Cloud Build → GHCR → Zeabur restart).

### 2026-05-23 — Crash loop after OpenRouter 403

**Symptom:** Container entered crash loop. Zeabur killed each pod seconds after `Started container hermes-sandbox`. Started after an OpenRouter HTTP 403 "Key limit exceeded" event; quota was increased but bot never recovered.

**Root causes found and fixed:**

1. **`_is_accepted_host()` wrong check order** (`hermes_cli/web_server.py`): When bound to `0.0.0.0`, Zeabur health probes use HTTP/1.0 (no `Host` header). The function returned 403 because the empty-host guard fired before the `0.0.0.0` bypass. Fixed by moving the `0.0.0.0` check to the top. Added `/health` endpoint as a dedicated probe target.

2. **Silent entrypoint failure for string-format `model` in config.yaml** (`docker/entrypoint.sh`): `cfg.setdefault("model", {})["default"] = value` raises `TypeError` when `cfg["model"]` is a plain string (as it is on the persistent volume from previous deploys). Error was swallowed by `except Exception`. Fixed by type-checking `model_val` and handling both string and dict formats explicitly.

3. **Startup pre-flight logging added** (`gateway/run.py`): Non-fatal log block before `runner.start()` that prints model, provider, and API key presence — makes misconfiguration visible in Zeabur logs immediately on startup without waiting for a first message.

**Resolution:** Rebuilt image, pushed to GHCR, restarted Zeabur service. Container stabilised on first boot.

### 2026-05-23 — Tools activation (web, image_gen, video_gen)

**Changes shipped:**

1. **Gateway auto-start** (`docker/entrypoint.sh`): After launching the dashboard in background, entrypoint polls `/health` (1s intervals, 30s timeout) then calls `hermes gateway restart` automatically. No manual "Restart Gateway" click needed after container restarts.

2. **Web tool (Exa)**: `EXA_API_KEY` added to entrypoint inject list. `web.backend=exa` forced into persistent `config.yaml` on every boot.

3. **image_gen (OpenRouter)**: New plugin `plugins/image_gen/openrouter/` using `OPENROUTER_API_KEY` (already present) + `openrouter.ai/api/v1/images/generations`. Default model: `black-forest-labs/flux.2-klein-4b` (~$0.014/image). HuggingFace plugin was attempted first but Zeabur blocks `router.huggingface.co` egress.

4. **video_gen (HuggingFace)**: New plugin `plugins/video_gen/huggingface/` using `damo-vilab/text-to-video-ms-1.7b` via `router.huggingface.co`. Note: stable-video-diffusion (SVD) is image-to-video only and not on HF free Serverless tier.

5. **Persistent config override** (`docker/entrypoint.sh`): `docker/config.yaml` is only seeded to the persistent volume on first boot. Added a boot-time patch that forces `web.backend`, `image_gen.provider`, and `video_gen.provider` into the volume's `config.yaml` on every restart.

6. **Egress diagnostics**: Entrypoint now logs HTTP status for `openrouter.ai` and `router.huggingface.co` on every boot, making Zeabur egress blocks immediately visible in container logs.

7. **`_ASPECT_TO_SIZE` / `response_format` fix** (`plugins/image_gen/openrouter/`): FLUX models on OpenRouter don't accept `response_format` or non-standard sizes. Payload simplified to `{model, prompt, n, size: "1024x1024"}`. Response parser now prefers `url` field (OpenRouter default) over `b64_json`.

### 2026-05-23 — OpenRouter plugin: switch to chat completions + grok-imagine

**Root cause**: `/api/v1/images/generations` endpoint broken/404 on OpenRouter.

**Changes applied in container then synced to repo:**
1. **Endpoint**: `/api/v1/images/generations` → `/api/v1/chat/completions`
2. **Payload**: `{model, prompt, n, size}` → `{model, modalities: ["image"], messages: [{role: user, content: prompt}]}`
3. **Default model**: `black-forest-labs/flux.2-klein-4b` → `x-ai/grok-imagine-image-quality`
4. **Response parsing**: `data[0].url` → `choices[0].message.images[0].image_url.url` (base64 data URL — strip `data:image/png;base64,` prefix, save to cache)
5. **docker/config.yaml**: added `image_gen.openrouter.model: x-ai/grok-imagine-image-quality`
6. **FLUX models** kept in catalog as selectable fallbacks

### 2026-05-23 — Image gen plugin audit & test coverage

1. **Dead code removed** (`plugins/image_gen/openrouter/`): `_ASPECT_TO_SIZE` dict was defined but never used (size is hardcoded `1024x1024` per FLUX OpenRouter limitation). Removed to keep the plugin clean.

2. **`plugin.yaml` corrected** (`plugins/image_gen/openrouter/`): Description still said "FLUX-schnell" but the active default model is `flux.2-klein-4b`. Updated.

3. **New test suites added**:
   - `tests/plugins/image_gen/test_openrouter_provider.py` — 17 tests covering metadata, availability, model resolution, generate (URL path, b64 path, API errors, network error, parse error, payload shape, aspect ratios), and registration.
   - `tests/plugins/image_gen/test_huggingface_provider.py` — 16 tests covering metadata, availability, model resolution, generate (successful save, 503 cold-start, API errors, network error, URL pattern, auth header, aspect ratios), and registration.

4. **Existing test fixes** (`tests/plugins/image_gen/test_openai_provider.py`, `test_openai_codex_provider.py`): 9 tests were failing due to missing `openai` package in the dev environment. Fixed by injecting a `MagicMock` into `sys.modules["openai"]` via pytest fixtures — tests now pass without requiring the optional `openai` package installed.
