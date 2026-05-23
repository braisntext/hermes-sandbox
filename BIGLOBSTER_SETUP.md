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

⚠️ Hermes does NOT autodeploy from git push. Zeabur is configured to pull a pre-built Docker image from GHCR.

### Why
Zeabur's build timeout is too short for the 2.7GB image (Playwright + Chromium + Python + Node). The build was consistently failing mid-run.

### Current flow (manual, until GitHub Actions is enabled)

1. Make changes to `hermes-sandbox` repo
2. Build and push image from local Mac:
   ```bash
   cd /Users/brais/VSCODE/hermes-sandbox
   docker buildx build --platform linux/amd64 -t ghcr.io/braisntext/hermes-sandbox:latest --push .
   ```
   First build: ~45 min on Apple Silicon. **Subsequent builds with cache: ~2-3 min** (only changed layers rebuild).
3. Restart Hermes service in Zeabur dashboard

### Future flow (once GitHub Actions is enabled by GitHub support)

GitHub Actions workflow `.github/workflows/ghcr-publish.yml` builds and pushes to GHCR automatically on every push to `main`. Zeabur still requires manual restart to pull the new image.

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

Set via `HERMES_DEFAULT_MODEL` env var in Zeabur — the entrypoint updates `config.yaml` on the persistent volume at every boot. The `docker/config.yaml` in the repo also reflects this as the default.

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

**Boot sequence:**
1. Container starts → dashboard launches on port 9119 (background)
2. Entrypoint polls `/health` (1s intervals, 30s max)
3. Once dashboard responds → `hermes gateway restart` runs automatically
4. Logs: `[entrypoint] Dashboard ready (Xs). Auto-starting gateway...` → `[entrypoint] Gateway auto-start complete.`

**To manually restart the gateway** (e.g. after changing Telegram env vars):
1. Go to `https://blhermes.zeabur.app`
2. Click **Restart Gateway** in the System section (bottom left)

Check status: Gateway Status should show **RUNNING** in the bottom-left of the panel.

---

## Mounted workspaces

The Hermes container's only writable volume is `/opt/data/` (Zeabur persistent volume, `HERMES_HOME=/opt/data`). The entrypoint pre-creates `/opt/data/workspace/` on boot.

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

Hermes activates tools based on configured API keys. The entrypoint forces provider settings into `config.yaml` on every boot.

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

After every Zeabur restart, confirm these lines appear in the logs:

```
[entrypoint] model.default already set to deepseek/deepseek-v4-flash
[entrypoint] web.backend already set to 'exa'
[entrypoint] image_gen.provider already set to 'openrouter'
[entrypoint] video_gen.provider already set to 'huggingface'
[entrypoint] Dashboard ready (Xs). Auto-starting gateway...
[entrypoint] Gateway auto-start complete.
[startup] Model: deepseek/deepseek-v4-flash | Provider: openrouter | API key present: True
```

Also check egress:
```
[entrypoint] Egress check: openrouter.ai 200
[entrypoint] Egress check: router.huggingface.co 200   (or FAIL if Zeabur blocks it)
```

If `API key present: False` or `Provider resolution FAILED` → check `OPENROUTER_API_KEY` in Zeabur Variables.

---

## Logs

Zeabur → hermes service → **Logs** tab.

For agent execution detail: `https://blhermes.zeabur.app/logs`

---

## Incident log

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

### 2026-05-23 — Image gen plugin audit & test coverage

1. **Dead code removed** (`plugins/image_gen/openrouter/`): `_ASPECT_TO_SIZE` dict was defined but never used (size is hardcoded `1024x1024` per FLUX OpenRouter limitation). Removed to keep the plugin clean.

2. **`plugin.yaml` corrected** (`plugins/image_gen/openrouter/`): Description still said "FLUX-schnell" but the active default model is `flux.2-klein-4b`. Updated.

3. **New test suites added**:
   - `tests/plugins/image_gen/test_openrouter_provider.py` — 17 tests covering metadata, availability, model resolution, generate (URL path, b64 path, API errors, network error, parse error, payload shape, aspect ratios), and registration.
   - `tests/plugins/image_gen/test_huggingface_provider.py` — 16 tests covering metadata, availability, model resolution, generate (successful save, 503 cold-start, API errors, network error, URL pattern, auth header, aspect ratios), and registration.

4. **Existing test fixes** (`tests/plugins/image_gen/test_openai_provider.py`, `test_openai_codex_provider.py`): 9 tests were failing due to missing `openai` package in the dev environment. Fixed by injecting a `MagicMock` into `sys.modules["openai"]` via pytest fixtures — tests now pass without requiring the optional `openai` package installed.
