# Prompt-patch — Slice 2: Open Graph + Core Web Vitals (onsite-SEO biglobster)

Prompt-only slice (NO helper, NO volume deploy). Adds two new actions to the live
`jobs.json` prompt. Apply via the hermes panel. Repo of-record:
`onsite-seo/seo-agent.prompt`.

Lesson carried from slice 1: both actions are PER-PAGE edits the agent does with
`read_file` / `patch` — no inline code, so nothing the cron sandbox blocks. No
`build_sitestate`-style helper needed.

---

## (A) Header — in `[DAILY WORKFLOW]`

- `**[DAILY WORKFLOW — 10 ACCIONES CORE]**`
- → `**[DAILY WORKFLOW — 11 ACCIONES CORE]**`
  (the header already said 10 while only 9 actions existed; now genuinely 11.)

## (B) Two new actions — insert after Acción 9, before the `---` / EXECUTION block

```
**Acción 10 — Open Graph y Twitter Cards (metadatos sociales)**
- Comprueba en el <head> de la URL las etiquetas Open Graph y Twitter Card. Si
  faltan o están incompletas, genera e inyecta (misma edición en <head> que
  Acciones 6 y 7):
  - og:title (= <title>/H1), og:description (= meta description), og:type
    (article en blog, website en landings), og:url (canónica), og:image (portada
    del artículo, URL ABSOLUTA https://biglobster.top/...), og:site_name (BigLobster).
  - twitter:card (summary_large_image), twitter:title, twitter:description,
    twitter:image (misma imagen que og:image).
- Deriva TODOS los valores del contenido YA presente (title, meta desc, portada,
  canonical). NUNCA inventes: sin portada identificable, OMITE og:image/twitter:image.
- Idempotencia: si las OG ya existen y son válidas, OMITE y documenta el skip.

**Acción 11 — Core Web Vitals (auditoría + fixes seguros)**
- Aplica SOLO el subconjunto SEGURO automáticamente; lo demás es ALERTA humana.
  NUNCA modifiques automáticamente la carga de JS/CSS (rompe el render).
  - SEGURO (auto): primera imagen (hero/LCP) carga eager (quita loading="lazy",
    añade fetchpriority="high"); imágenes siguientes sin loading → loading="lazy".
  - ALERTA (solo reporta): <img> sin width/height (CLS); <script> de terceros sin
    defer/async; CSS bloqueante; falta de <link rel="preload"> crítico.
- Idempotencia: no re-apliques loading/fetchpriority si ya están.
```

## (C) Learning schema enum — in `[LEARNING & FEEDBACK LOOP]`

Add `open_graph | cwv` to the `tipo_accion` list.

---

## Rollout note (backfill existing pages)

New/edited pages get OG+CWV from their next audit automatically. To BACKFILL the
existing ~66 pages, register OG as a propagation rule in `[REGISTRO DE REGLAS]`
(human action, per `[GOBERNANZA DE NUEVAS REGLAS]`) so PROPAGACIÓN mode sweeps it in
gradually under the per-cycle limits — not a mass change. CWV stays opportunistic
(applied when a page is audited for other reasons), not a propagation rule, since
most of it is advisory.

## Verify after applying
- A run that audits a page shows `tipo_accion: open_graph` / `cwv` in the report.
- OG tags appear ONLY in <head>, derived from real page content (no placeholders).
- No `<script>` defer/async/preload auto-edits — those appear only as ALERTA.
- Zero-Break Policy still passes (head-meta edits are allowed, like Acción 6/7).
