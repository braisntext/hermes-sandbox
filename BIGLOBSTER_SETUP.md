# Hermes Architect Agent — BigLobster Integration

Hermes runs as a service on Zeabur (same project as BigLobster) and communicates over the internal network. The COO delegates long-running coding tasks to it asynchronously.

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
| Repo | `github.com/braisntext/hermes-sandbox` |
| Deploy | Push to `main` → Zeabur auto-deploys |

---

## Zeabur environment variables

Set these in Zeabur → hermes service → **Environment Variables**:

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENROUTER_API_KEY` | Yes | OpenRouter API key |
| `HERMES_CALLBACK_SECRET` | Yes | Shared secret for callback auth. Generate: `openssl rand -hex 32`. Must match `HERMES_CALLBACK_SECRET` in the BigLobster service. |
| `HERMES_CALLBACK_URL` | Yes | `https://biglobster.top/api/hermes-callback` |
| `HERMES_MAX_ITERATIONS` | No | Max agent turns per task (default: 60) |

Set these in Zeabur → **BigLobster** service → **Environment Variables**:

| Variable | Value |
|----------|-------|
| `HERMES_URL` | `http://hermes-sandbox.zeabur.internal:9119` |
| `HERMES_CALLBACK_SECRET` | Same value as above |

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

## Smoke test

From inside the BigLobster service (or any Zeabur service in the same project):

```bash
# Health check
curl http://hermes-sandbox.zeabur.internal:9119/api/status

# Manual delegate
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

## Logs

In Zeabur → hermes service → **Logs**.

Or from CLI:

```bash
zeabur service logs hermes
```
