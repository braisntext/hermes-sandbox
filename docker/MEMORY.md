GitHub HTTPS auth: password auth is disabled. Always inject GITHUB_TOKEN. Use: git clone https://x-access-token:$GITHUB_TOKEN@github.com/<owner>/<repo>.git — or run the github-auth skill to set up credential.helper store so the token persists across git calls.
§
GITHUB_TOKEN is available as an env var (set via Zeabur) and in /opt/data/.env. Before any git push/clone/fetch, verify it is set: [ -n "$GITHUB_TOKEN" ] || echo "GITHUB_TOKEN missing". If missing, ask the user to set it in the Zeabur env var panel.
§
git identity must be configured before any commit: git config --global user.name "Hermes Agent" && git config --global user.email "hermes@agent.local". Check first with: git config --global user.name.
§
ping is not available in this container. Use curl for connectivity checks: curl -sf --max-time 5 https://github.com -o /dev/null && echo OK || echo FAIL
§
vision tool: AUXILIARY_VISION_MODEL env var sets the vision model. owl-alpha does not support image input — if vision fails with 404, the tool auto-retries with the default backend (google/gemini-3-flash-preview via OpenRouter). Check AUXILIARY_VISION_MODEL value before debugging vision failures.
§
Memory limit is 6000 chars. When near-full, compact entries: merge related facts, drop stale context. Never let memory fill to capacity — future writes will be silently rejected.
