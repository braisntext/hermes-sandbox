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
   ⚠️ Takes ~45 min on Apple Silicon (arm64). Run in background.
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

The Hermes gateway (HTTP server on port 9119) must be running for BigLobster delegation to work. It does NOT start automatically with the container — it is managed by the web dashboard.

**To start/restart the gateway:**
1. Go to `https://blhermes.zeabur.app`
2. Click **Restart Gateway** in the System section (bottom left)

Check status: Gateway Status should show **RUNNING** in the bottom-left of the panel.

---

## Mounted workspaces

The Hermes container has read/write access to these repos via Zeabur volumes:

| Container path | Repo |
|----------------|------|
| `/workspace/FinView` | FinView |
| `/workspace/WorldHawk` | WorldHawk |
| `/workspace/grow-shop` | grow-shop |
| `/workspace/bl-site-package` | bl-site-package |
| `/workspace/biglobster` | biglobster |

---

## Skills

Reusable Hermes skills live in `/opt/data/skills/` on the persistent volume.

| Skill | Path | Description |
|-------|------|-------------|
| `prospeccion-local` | `research/prospeccion-local/SKILL.md` | Geographic lead prospecting for BigLobster ICP. Searches local businesses by zone, extracts contacts, filters Ourense city, POSTs to `/api/hermes-leads`. Usage: "Prospecta el polígono de San Cibrao das Viñas" |

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
    "prompt": "List files in /workspace/biglobster and return a summary.",
    "webhook_url": "https://biglobster.top/api/hermes-callback"
  }'
# Expected: {"task_id":"test-001","status":"accepted"}
```

---

## Startup log verification

After every Zeabur restart, confirm these lines appear in the logs before clicking Restart Gateway:

```
[entrypoint] model.default already set to deepseek/deepseek-v4-flash
```

After clicking Restart Gateway in the web panel, confirm:

```
[startup] Model: deepseek/deepseek-v4-flash | Provider: openrouter | API key present: True
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
