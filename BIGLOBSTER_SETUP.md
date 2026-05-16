# Hermes Architect Agent — BigLobster Integration

This guide covers running Hermes on your Mac and wiring it to BigLobster (Zeabur) so the COO can delegate long-running coding tasks.

## How it works

```
BigLobster COO (Zeabur)
  → POST /api/delegate  →  Hermes (Mac, exposed via ngrok)
                               └─ runs agent in background thread
  ← POST /api/hermes-callback  (webhook, async when done)
BigLobster sentinel.js
  → notifyCeo() → Telegram message to CEO
```

---

## Prerequisites

| Tool | Install |
|------|---------|
| Docker Desktop | https://www.docker.com/products/docker-desktop |
| ngrok | `brew install ngrok/ngrok/ngrok` |
| ngrok account | https://dashboard.ngrok.com (free tier is enough) |

---

## First-time setup

### 1. Configure your API key

Edit `.env.sandbox` and fill in at least one LLM provider key:

```bash
OPENROUTER_API_KEY=sk-or-v1-...   # recommended
```

### 2. Set the shared secret

Generate a random secret that matches the one in Zeabur:

```bash
openssl rand -hex 32
```

Copy the output into `.env.sandbox`:

```
HERMES_CALLBACK_SECRET=<generated-value>
```

Then set the **same value** in Zeabur → BigLobster → Environment Variables → `HERMES_CALLBACK_SECRET`.

### 3. Authenticate ngrok

```bash
ngrok config add-authtoken <your-ngrok-token>
```

---

## Starting Hermes

```bash
cd ~/VSCODE/hermes-sandbox
./start-dev.sh
```

The script:
1. Checks Docker and ngrok are available
2. Builds and starts the Docker container (`hermes-sandbox`)
3. Waits for the dashboard to be ready on port 9119
4. Starts ngrok and extracts the public HTTPS URL
5. Prints the `HERMES_URL` value you need to set in Zeabur

Example output:
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  🏗️  Hermes Architect Agent — ACTIVO
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  URL pública (ngrok): https://xxxx-xx-xx.ngrok-free.app

  ┌─ Actualiza en Zeabur (BigLobster → Variables de entorno):
  │  HERMES_URL=https://xxxx-xx-xx.ngrok-free.app
  └─ Luego haz Redeploy en Zeabur.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## Connecting to Zeabur (BigLobster)

1. Copy the `HERMES_URL` from the script output
2. Go to Zeabur → BigLobster service → **Variables de entorno**
3. Set `HERMES_URL=https://xxxx.ngrok-free.app`
4. Click **Redeploy**

> The ngrok URL changes every time you restart ngrok (free tier). Repeat steps 1–4 each session.

---

## Smoke test

With Hermes running locally, run these in a separate terminal:

```bash
# 1. Health check
curl http://localhost:9119/api/status

# 2. Manual delegate call (replace webhook_url with a public endpoint or use ngrok's own URL)
curl -X POST http://localhost:9119/api/delegate \
  -H "Content-Type: application/json" \
  -d '{
    "task_id": "test-001",
    "prompt": "List the files in the /workspace directory and return a brief summary.",
    "webhook_url": "https://biglobster.top/api/hermes-callback"
  }'
# Expected: {"task_id":"test-001","status":"accepted"}
```

Then send a message in Telegram to the COO:
> "Delega a Hermes: lista los archivos en /workspace y dame un resumen."

The COO will call `delegate_to_architect`, Hermes processes the task asynchronously, and BigLobster sends a Telegram notification when done.

---

## Stopping Hermes

Press `Ctrl+C` in the terminal running `./start-dev.sh`. The cleanup trap stops ngrok and brings down the Docker container automatically.

To stop the container manually without the script:

```bash
docker compose -f docker-compose.sandbox.yml down
```

---

## Logs

```bash
# Container logs (agent output, errors)
docker logs -f hermes-sandbox

# ngrok tunnel log
tail -f /tmp/hermes-ngrok.log

# ngrok inspector UI
open http://localhost:4040
```

---

## Mounted project directories

The container has read/write access to these repos at `/workspace/`:

| Host path | Container path |
|-----------|---------------|
| `~/VSCODE/FinView` | `/workspace/FinView` |
| `~/VSCODE/WorldHawk` | `/workspace/WorldHawk` |
| `~/VSCODE/grow-shop` | `/workspace/grow-shop` |
| `~/VSCODE/bl-site-package` | `/workspace/bl-site-package` |
| `~/VSCODE/biglobster` | `/workspace/biglobster` |

To add more repos, edit `docker-compose.sandbox.yml` under `volumes:`.

---

## Environment variables reference

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENROUTER_API_KEY` | Yes* | OpenRouter API key (or use another provider) |
| `HERMES_CALLBACK_SECRET` | Recommended | Shared secret for webhook authentication |
| `TERMINAL_ENV` | No | `local` (default) — agent runs shell commands inside container |
| `WEB_TOOLS_DEBUG` | No | `true` to enable verbose web tool logs |

*At least one LLM provider key is required.
