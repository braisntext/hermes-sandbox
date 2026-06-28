# Backfill enablement — rule registry + PROPAGACIÓN (onsite-SEO biglobster)

Wires up `[REGISTRO DE REGLAS]` (which the prompt referenced but never gave a storage
path) so backfill becomes a real, one-step action. Then registers an Open Graph rule
so the agent sweeps the existing ~66 pages, 3 per run, until all have OG.

Two parts: (1) prompt edits → live `jobs.json` (panel); (2) create the rule file on
the volume. Repo of-record: `onsite-seo/seo-agent.prompt` + `seo-rules.example.json`.

---

## (1) Prompt edits (apply to live jobs.json)

**A — `[ESTADO DE MADUREZ]`, step 2:** give the registry a path.
- old: `Cuenta las reglas en [REGISTRO DE REGLAS] con propagacion_pendiente = true.`
- new: add `(/opt/data/profiles/biglobster/seo/seo-rules.json, array JSON; si no existe = sin reglas)` after `[REGISTRO DE REGLAS]`.

**B — `[GOBERNANZA DE NUEVAS REGLAS]`, step 2:** after "la registra en [REGISTRO DE REGLAS]." insert:
```
Ruta canónica del registro: /opt/data/profiles/biglobster/seo/seo-rules.json, un
ARRAY JSON de reglas. Léelo al inicio de cada ciclo (junto al ledger) para determinar
el modo; si no existe, trátalo como []. Para LEER/ESCRIBIR usa read_file / write_file /
terminal (cat), NUNCA execute_code ni python3 -c/-e.
```

**C — `[GOBERNANZA]`, step 3:** scope propagation to the rule's action (not a full
re-audit) and write completion back to seo-rules.json:
```
A cada URL aplícale SOLO la accion_asociada de la regla más las acciones técnicas
seguras (NO una reauditoría completa), para acotar el blast radius; tras el push,
añade el rule_id a reglas_cumplidas de esa URL en el ledger. … Al cubrir la última URL
del alcance, marca propagacion_pendiente = false en seo-rules.json.
```

**D — `[PRE-FLIGHT]`:** `BATCH_SIZE = 1` → `BATCH_SIZE = 3`.

(Full text already in `onsite-seo/seo-agent.prompt`.)

---

## (2) Create the rule file on the volume (as root, then chown)

The dir is hermes-owned; a file created by root is root-owned and the agent can't
write `propagacion_pendiente=false` back → same Permission-denied gotcha as the helper.
So chown after creating.

```bash
cat > /opt/data/profiles/biglobster/seo/seo-rules.json <<'JSONEOF'
[
  {
    "rule_id": "RULE-2026-001",
    "descripcion": "Toda URL debe tener etiquetas Open Graph y Twitter Card completas (Acción 10), derivadas del contenido real de la página.",
    "alcance": "todas",
    "accion_asociada": "open_graph",
    "fecha_alta": "2026-06-28",
    "propagacion_pendiente": true
  }
]
JSONEOF
chown hermes:hermes /opt/data/profiles/biglobster/seo/seo-rules.json
python3 -c "import json;print('rules OK:', len(json.load(open('/opt/data/profiles/biglobster/seo/seo-rules.json'))))"
```

---

## What happens next

- Next run: backlog=0 + a pending rule → mode **PROPAGACIÓN**. The agent processes up
  to **3** in-scope URLs/run that lack `RULE-2026-001` in their `reglas_cumplidas`,
  applying Acción 10 (OG) + safe technical actions, committing per-URL.
- ~66 pages ÷ 3 ≈ **22 runs** to finish. When the last is covered, the agent flips
  `propagacion_pendiente=false` and returns to MANTENIMIENTO.
- After backfill you may drop `BATCH_SIZE` back to 1 (optional; 3 is also fine steady-state).

## Verify (next trace)
- Mode = **PROPAGACIÓN**, batch of up to 3 URLs.
- Per audited URL: `tipo_accion: open_graph`, OG/Twitter tags injected in `<head>` from
  real page content, `reglas_cumplidas` gains `RULE-2026-001`.
- No script defer/async/preload auto-edits (CWV stays alert-only).
