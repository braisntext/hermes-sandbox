#!/bin/bash
# Docker/Podman entrypoint: bootstrap config files into the mounted volume, then run hermes.
set -e

HERMES_HOME="${HERMES_HOME:-/opt/data}"
INSTALL_DIR="/opt/hermes"

# --- Privilege dropping via gosu ---
# When started as root (the default for Docker, or fakeroot in rootless Podman),
# optionally remap the hermes user/group to match host-side ownership, fix volume
# permissions, then re-exec as hermes.
if [ "$(id -u)" = "0" ]; then
    if [ -n "$HERMES_UID" ] && [ "$HERMES_UID" != "$(id -u hermes)" ]; then
        echo "Changing hermes UID to $HERMES_UID"
        usermod -u "$HERMES_UID" hermes
    fi

    if [ -n "$HERMES_GID" ] && [ "$HERMES_GID" != "$(id -g hermes)" ]; then
        echo "Changing hermes GID to $HERMES_GID"
        # -o allows non-unique GID (e.g. macOS GID 20 "staff" may already exist
        # as "dialout" in the Debian-based container image)
        groupmod -o -g "$HERMES_GID" hermes 2>/dev/null || true
    fi

    # Fix ownership of the data volume. When HERMES_UID remaps the hermes user,
    # files created by previous runs (under the old UID) become inaccessible.
    # Always chown -R when UID was remapped; otherwise only if top-level is wrong.
    actual_hermes_uid=$(id -u hermes)
    needs_chown=false
    if [ -n "$HERMES_UID" ] && [ "$HERMES_UID" != "10000" ]; then
        needs_chown=true
    elif [ "$(stat -c %u "$HERMES_HOME" 2>/dev/null)" != "$actual_hermes_uid" ]; then
        needs_chown=true
    fi
    if [ "$needs_chown" = true ]; then
        echo "Fixing ownership of $HERMES_HOME to hermes ($actual_hermes_uid)"
        # In rootless Podman the container's "root" is mapped to an unprivileged
        # host UID — chown will fail.  That's fine: the volume is already owned
        # by the mapped user on the host side.
        chown -R hermes:hermes "$HERMES_HOME" 2>/dev/null || \
            echo "Warning: chown failed (rootless container?) — continuing anyway"
        # The .venv must also be re-chowned when UID is remapped, otherwise
        # lazy_deps.py cannot install platform packages (discord.py, etc.).
        chown -R hermes:hermes "$INSTALL_DIR/.venv" 2>/dev/null || \
            echo "Warning: chown .venv failed (rootless container?) — continuing anyway"
    fi

    # Ensure config.yaml is readable by the hermes runtime user even if it was
    # edited on the host after initial ownership setup. Must run here (as root)
    # rather than after the gosu drop, otherwise a non-root caller like
    # `docker run -u $(id -u):$(id -g)` hits "Operation not permitted" (#15865).
    if [ -f "$HERMES_HOME/config.yaml" ]; then
        chown hermes:hermes "$HERMES_HOME/config.yaml" 2>/dev/null || true
        chmod 640 "$HERMES_HOME/config.yaml" 2>/dev/null || true
    fi

    # Fix ownership of cron/jobs.json — if a previous run or a root-level
    # docker exec created it as root:root (mode 600), the hermes runtime user
    # cannot read its own cron jobs after a redeploy. See issue #1.
    if [ -f "$HERMES_HOME/cron/jobs.json" ]; then
        chown hermes:hermes "$HERMES_HOME/cron/jobs.json" 2>/dev/null || true
        chmod 640 "$HERMES_HOME/cron/jobs.json" 2>/dev/null || true
    fi

    # Fix ownership of gateway.lock — if a previous container run left the lock
    # owned by a different UID (host user, root, etc.), opening it with "a+"
    # fails with PermissionError and the gateway cannot start.
    if [ -f "$HERMES_HOME/gateway.lock" ]; then
        chown hermes:hermes "$HERMES_HOME/gateway.lock" 2>/dev/null || true
        chmod 640 "$HERMES_HOME/gateway.lock" 2>/dev/null || true
    fi

    echo "Dropping root privileges"
    exec gosu hermes "$0" "$@"
fi

# --- Running as hermes from here ---
source "${INSTALL_DIR}/.venv/bin/activate"

# Create essential directory structure.  Cache and platform directories
# (cache/images, cache/audio, platforms/whatsapp, etc.) are created on
# demand by the application — don't pre-create them here so new installs
# get the consolidated layout from get_hermes_dir().
# The "home/" subdirectory is a per-profile HOME for subprocesses (git,
# ssh, gh, npm …).  Without it those tools write to /root which is
# ephemeral and shared across profiles.  See issue #4426.
mkdir -p "$HERMES_HOME"/{cron,sessions,logs,hooks,memories,skills,skins,plans,workspace,home}

# .env
if [ ! -f "$HERMES_HOME/.env" ]; then
    cp "$INSTALL_DIR/.env.example" "$HERMES_HOME/.env"
fi

# Inject Zeabur (process) env vars into .env on every boot.
# env_loader.py uses load_dotenv(override=True), so the .env file wins over
# the process environment. We sync the values here so Zeabur-injected vars
# (OPENROUTER_API_KEY, HERMES_CALLBACK_SECRET, etc.) are always picked up.
python3 - <<'PYEOF'
import os, re
from pathlib import Path

env_path = Path(os.environ["HERMES_HOME"]) / ".env"
inject = [
    "OPENROUTER_API_KEY",
    "HERMES_CALLBACK_SECRET",
    "HERMES_CALLBACK_URL",
    "HERMES_MAX_ITERATIONS",
    "EXA_API_KEY",
    "HUGGINGFACE_API_KEY",
    "GITHUB_TOKEN",
    "AUXILIARY_VISION_MODEL",
]

content = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
for var in inject:
    val = os.environ.get(var, "")
    if not val:
        continue
    if re.search(rf"^{re.escape(var)}=", content, re.MULTILINE):
        content = re.sub(rf"^{re.escape(var)}=.*", f"{var}={val}", content, flags=re.MULTILINE)
    else:
        content += f"\n{var}={val}"
env_path.write_text(content, encoding="utf-8")
print(f"[entrypoint] Injected env vars into {env_path}")

# Also update model in config.yaml if HERMES_DEFAULT_MODEL is set.
# Handles both string ("model: deepseek/deepseek-v4-flash") and dict
# ("model: {default: ..., provider: ...}") formats in config.yaml.
import yaml
default_model = os.environ.get("HERMES_DEFAULT_MODEL", "")
config_path = Path(os.environ["HERMES_HOME"]) / "config.yaml"
if default_model and config_path.exists():
    try:
        cfg = yaml.safe_load(config_path.read_text()) or {}
        model_val = cfg.get("model")
        current_default = None
        if isinstance(model_val, dict):
            current_default = model_val.get("default")
        elif isinstance(model_val, str):
            current_default = model_val.strip()
        if current_default != default_model:
            if isinstance(model_val, dict):
                model_val["default"] = default_model
                cfg["model"] = model_val
            else:
                # Upgrade string format to dict so provider/base_url can coexist
                cfg["model"] = {"default": default_model, "provider": "openrouter", "base_url": ""}
            config_path.write_text(yaml.dump(cfg, default_flow_style=False))
            print(f"[entrypoint] Updated model.default to {default_model} (was: {current_default!r})")
        else:
            print(f"[entrypoint] model.default already set to {default_model}")
    except Exception as e:
        print(f"[entrypoint] Warning: could not update config.yaml: {e}")
elif default_model:
    print(f"[entrypoint] HERMES_DEFAULT_MODEL={default_model} set but config.yaml not found yet (will be created below)")
else:
    print("[entrypoint] HERMES_DEFAULT_MODEL not set — using existing config.yaml model")

# Force tool provider settings into the persistent config.yaml on every boot.
# docker/config.yaml is the source of truth, but the persistent volume
# config.yaml is only copied from it on FIRST boot — subsequent reboots keep
# whatever is already on the volume.  We patch the three keys here so they
# always match the intended values regardless of how old the volume config is.
#
# Keys patched:
#   web.backend              = "exa"          (requires EXA_API_KEY)
#   image_gen.provider       = "huggingface"  (requires HUGGINGFACE_API_KEY)
#   video_gen.provider       = "huggingface"  (requires HUGGINGFACE_API_KEY)
#   memory.memory_char_limit = 6000           (enough for multi-repo workflows)
#   memory.user_char_limit   = 3000           (proportional to memory_char_limit)
_tool_overrides = {
    ("web", "backend"): "exa",
    ("image_gen", "provider"): "openrouter",
    ("video_gen", "provider"): "huggingface",
    ("memory", "memory_char_limit"): 6000,
    ("memory", "user_char_limit"): 3000,
}
if config_path.exists():
    try:
        _cfg = yaml.safe_load(config_path.read_text()) or {}
        _changed = False
        for (_section, _key), _val in _tool_overrides.items():
            if not isinstance(_cfg.get(_section), dict):
                _cfg[_section] = {}
            if _cfg[_section].get(_key) != _val:
                _cfg[_section][_key] = _val
                _changed = True
                print(f"[entrypoint] Set {_section}.{_key}={_val} in config.yaml")
            else:
                print(f"[entrypoint] {_section}.{_key} already set to {_val!r}")
        if _changed:
            config_path.write_text(yaml.dump(_cfg, default_flow_style=False))
    except Exception as _e:
        print(f"[entrypoint] Warning: could not update tool providers in config.yaml: {_e}")
PYEOF

# --- Egress connectivity diagnostics ---
# Logs reachability of key external domains so Zeabur egress blocks are
# visible in container logs without needing a separate debug deploy.
echo "[entrypoint] Egress check: openrouter.ai $(curl -sf --max-time 5 -o /dev/null -w '%{http_code}' https://openrouter.ai/ 2>/dev/null || echo 'FAIL')"
echo "[entrypoint] Egress check: router.huggingface.co $(curl -sf --max-time 5 -o /dev/null -w '%{http_code}' https://router.huggingface.co/ 2>/dev/null || echo 'FAIL')"

# config.yaml
if [ ! -f "$HERMES_HOME/config.yaml" ]; then
    if [ -f "$INSTALL_DIR/docker/config.yaml" ]; then
        cp "$INSTALL_DIR/docker/config.yaml" "$HERMES_HOME/config.yaml"
    else
        cp "$INSTALL_DIR/cli-config.yaml.example" "$HERMES_HOME/config.yaml"
    fi
fi

# SOUL.md
if [ ! -f "$HERMES_HOME/SOUL.md" ]; then
    cp "$INSTALL_DIR/docker/SOUL.md" "$HERMES_HOME/SOUL.md"
fi

# MEMORY.md: seed agent memory on first boot if a seed file exists.
# This gives fresh deploys built-in knowledge about the environment
# (git auth, known repos, workspace conventions) without wasting context
# or waiting for the agent to rediscover it.
if [ ! -f "$HERMES_HOME/memories/MEMORY.md" ] && [ -f "$INSTALL_DIR/docker/MEMORY.md" ]; then
    cp "$INSTALL_DIR/docker/MEMORY.md" "$HERMES_HOME/memories/MEMORY.md"
    echo "[entrypoint] Seeded agent memory from docker/MEMORY.md"
fi

# auth.json: bootstrap from env on first boot only.  Used by orchestrators
# (e.g. provisioning a Hermes VPS from an account-management service) that
# need to seed the OAuth refresh credential non-interactively, instead of
# walking the user through `hermes setup` + the device-flow login dance.
# Subsequent token rotations write back to the same file, which lives on a
# persistent volume — so this env var is consumed exactly once at first
# boot.  The `[ ! -f ... ]` guard is critical: without it, a container
# restart would clobber a rotated refresh token with the now-stale value
# the orchestrator originally seeded.
if [ ! -f "$HERMES_HOME/auth.json" ] && [ -n "$HERMES_AUTH_JSON_BOOTSTRAP" ]; then
    printf '%s' "$HERMES_AUTH_JSON_BOOTSTRAP" > "$HERMES_HOME/auth.json"
    chmod 600 "$HERMES_HOME/auth.json"
fi

# Sync bundled skills (manifest-based so user edits are preserved)
if [ -d "$INSTALL_DIR/skills" ]; then
    python3 "$INSTALL_DIR/tools/skills_sync.py"
fi

# Optionally start `hermes dashboard` as a side-process.
#
# Toggled by HERMES_DASHBOARD=1 (also accepts "true"/"yes", case-insensitive).
# Host/port/TUI can be overridden via:
#   HERMES_DASHBOARD_HOST  (default 0.0.0.0 — exposed outside the container)
#   HERMES_DASHBOARD_PORT  (default 9119, matches `hermes dashboard` default)
#   HERMES_DASHBOARD_TUI   (already honored by `hermes dashboard` itself)
#
# The dashboard is a long-lived server.  We background it *before* the final
# `exec hermes "$@"` so the user's chosen foreground command (chat, gateway,
# sleep infinity, …) remains PID-of-interest for the container runtime.  When
# the container stops the whole process tree is torn down, so no explicit
# cleanup is needed.
case "${HERMES_DASHBOARD:-}" in
    1|true|TRUE|True|yes|YES|Yes)
        dash_host="${HERMES_DASHBOARD_HOST:-0.0.0.0}"
        dash_port="${HERMES_DASHBOARD_PORT:-9119}"
        dash_args=(--host "$dash_host" --port "$dash_port" --no-open)
        # Binding to anything other than localhost requires --insecure — the
        # dashboard refuses otherwise because it exposes API keys.  Inside a
        # container this is the expected deployment (host reaches it via
        # published port), so opt in automatically.
        if [ "$dash_host" != "127.0.0.1" ] && [ "$dash_host" != "localhost" ]; then
            dash_args+=(--insecure)
        fi
        echo "Starting hermes dashboard on ${dash_host}:${dash_port} (background)"
        # Prefix dashboard output so it's distinguishable from the main
        # process in `docker logs`.  stdbuf keeps the pipe line-buffered.
        (
            stdbuf -oL -eL hermes dashboard "${dash_args[@]}" 2>&1 \
                | sed -u 's/^/[dashboard] /'
        ) &

        # Auto-start the gateway once the dashboard is responsive.
        # Polls /health at 1s intervals (max 30s), then runs `hermes gateway
        # restart`.  The whole block runs in a background subshell so the
        # final `exec sleep infinity` proceeds immediately and the container
        # never stalls waiting for the dashboard to warm up.
        #
        # `hermes gateway restart` is idempotent: with no service manager
        # (Docker / Zeabur) it stops any existing gateway process and starts a
        # fresh one.  On first boot nothing is running, so it's a clean start.
        (
            _gw_port="${HERMES_DASHBOARD_PORT:-9119}"
            echo "[entrypoint] Waiting for dashboard on port ${_gw_port}..."
            _gw_waited=0
            _gw_ready=false
            while [ "$_gw_waited" -lt 30 ]; do
                if curl -sf "http://localhost:${_gw_port}/health" >/dev/null 2>&1; then
                    _gw_ready=true
                    break
                fi
                sleep 1
                _gw_waited=$((_gw_waited + 1))
            done
            if [ "$_gw_ready" = true ]; then
                echo "[entrypoint] Dashboard ready (${_gw_waited}s). Auto-starting gateway..."
                hermes gateway restart 2>&1 | sed -u 's/^/[gateway-autostart] /'
                echo "[entrypoint] Gateway auto-start complete."
            else
                echo "[entrypoint] Warning: dashboard did not respond after 30s — gateway NOT auto-started. Container will remain alive; start manually via the web panel."
            fi
        ) &
        ;;
esac

# Final exec: two supported invocation patterns.
#
#   docker run <image>                 -> exec `hermes` with no args (legacy default)
#   docker run <image> chat -q "..."   -> exec `hermes chat -q "..."` (legacy wrap)
#   docker run <image> sleep infinity  -> exec `sleep infinity` directly
#   docker run <image> bash            -> exec `bash` directly
#
# If the first positional arg resolves to an executable on PATH, we assume the
# caller wants to run it directly (needed by the launcher which runs long-lived
# `sleep infinity` sandbox containers — see tools/environments/docker.py).
# Otherwise we treat the args as a hermes subcommand and wrap with `hermes`,
# preserving the documented `docker run <image> <subcommand>` behavior.
if [ $# -gt 0 ] && command -v "$1" >/dev/null 2>&1; then
    exec "$@"
fi
exec hermes "$@"
