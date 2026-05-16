#!/usr/bin/env bash
# start-dev.sh — Arranca Hermes Architect Agent en local con ngrok
# Uso: ./start-dev.sh
# Requisitos: Docker Desktop, ngrok (brew install ngrok/ngrok/ngrok)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.sandbox.yml"

# ─── Checks ──────────────────────────────────────────────────────────────────

if ! command -v docker &>/dev/null; then
  echo "❌ Docker no encontrado. Instala Docker Desktop: https://www.docker.com/products/docker-desktop/"
  exit 1
fi
if ! docker info &>/dev/null 2>&1; then
  echo "❌ Docker Desktop no está corriendo. Ábrelo y vuelve a intentarlo."
  exit 1
fi
if ! command -v ngrok &>/dev/null; then
  echo "❌ ngrok no encontrado. Instala: brew install ngrok/ngrok/ngrok"
  exit 1
fi
if [ ! -f "$SCRIPT_DIR/.env.sandbox" ]; then
  echo "❌ Falta .env.sandbox. Cópialo de .env.sandbox.example y rellena OPENROUTER_API_KEY."
  exit 1
fi

# ─── Arrancar Hermes ──────────────────────────────────────────────────────────

echo ""
echo "🏗️  Arrancando Hermes Architect Agent..."
echo ""

cd "$SCRIPT_DIR"

# Build + start (--build solo reconstruye si hay cambios en el Dockerfile o fuentes)
HERMES_UID=$(id -u) HERMES_GID=$(id -g) \
  docker compose -f "$COMPOSE_FILE" up -d --build 2>&1 | grep -v "^#" || true

echo ""
echo "⏳ Esperando que el dashboard arranque en puerto 9119..."
READY=0
for i in $(seq 1 60); do
  if curl -sf http://localhost:9119/api/status >/dev/null 2>&1; then
    READY=1
    break
  fi
  sleep 2
done

if [ $READY -eq 0 ]; then
  echo "⚠️  Hermes tardando más de lo esperado."
  echo "   Revisa los logs: docker logs hermes-sandbox"
  echo "   (puede ser la primera build — tarda ~10 min)"
  echo ""
fi

# ─── Arrancar ngrok ───────────────────────────────────────────────────────────

# Matar ngrok previo si existe
pkill -f "ngrok http 9119" 2>/dev/null || true
sleep 1

echo "🌐 Iniciando ngrok en puerto 9119..."
ngrok http 9119 --log=stdout --log-format=json >/tmp/hermes-ngrok.log 2>&1 &
NGROK_PID=$!

# Extraer la URL pública de la API local de ngrok
NGROK_URL=""
for i in $(seq 1 20); do
  sleep 1
  NGROK_URL=$(curl -s http://localhost:4040/api/tunnels 2>/dev/null \
    | python3 -c "
import sys, json
try:
    tunnels = json.load(sys.stdin).get('tunnels', [])
    url = next((t['public_url'] for t in tunnels if t.get('proto') == 'https'), '')
    print(url)
except Exception:
    print('')
" 2>/dev/null || echo "")
  [ -n "$NGROK_URL" ] && break
done

# ─── Resultado ────────────────────────────────────────────────────────────────

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  🏗️  Hermes Architect Agent — ACTIVO"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [ -n "$NGROK_URL" ]; then
  echo "  URL pública (ngrok): $NGROK_URL"
  echo ""
  echo "  ┌─ Actualiza en Zeabur (BigLobster → Variables de entorno):"
  echo "  │  HERMES_URL=$NGROK_URL"
  echo "  └─ Luego haz Redeploy en Zeabur."
else
  echo "  ⚠️  No se pudo obtener URL de ngrok automáticamente."
  echo "  Abre http://localhost:4040 para ver la URL pública."
fi
echo ""
echo "  Health check local:"
echo "    curl http://localhost:9119/api/status"
if [ -n "$NGROK_URL" ]; then
  echo "  Health check remoto:"
  echo "    curl $NGROK_URL/api/status"
fi
echo ""
echo "  Logs del contenedor: docker logs -f hermes-sandbox"
echo "  Para parar todo:     Ctrl+C"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# ─── Cleanup al salir ────────────────────────────────────────────────────────

cleanup() {
  echo ""
  echo "⏹  Parando Hermes y ngrok..."
  kill "$NGROK_PID" 2>/dev/null || true
  docker compose -f "$COMPOSE_FILE" down
  echo "✅ Parado."
}
trap cleanup EXIT INT TERM
wait "$NGROK_PID" 2>/dev/null || true
