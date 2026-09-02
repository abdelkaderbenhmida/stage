# web/static

The buildless SPA: plain scripts loaded in order by `index.html`, no bundler. Shared
modules are IIFEs hanging one object off `window`. CSP is `script-src 'self'` — no
inline handlers; bind via `el.onclick = fn` (control-plane console) or `data-act`
dispatch (platform console).

- `index.html` — the one HTML entrypoint; loads `shell.css` first, then
  `platform/style.css`, then `style.css`; declares `color-scheme: dark` (the console
  ships one dark theme only). Hash routing (`#/...`) picks the view client-side.
- `app.js` — the control-plane SPA: hash routing, vanilla DOM, talks to `/api/v1` with a
  bearer token, same as any API client.
- `graph.js` — `window.PipelineGraph`, the one shared pipeline-graph renderer used by
  both the tenant deployment view and the platform CI view. Deterministic layered
  (Sugiyama-lite) layout, no `Math.random`.
- `shell.css` — the global stylesheet: palette, page background, CSS reset. The one
  stylesheet not scoped to a subtree. Note: `style.css:279-333` contains a known leaked
  block — leave it alone. Pipeline-graph classes are prefixed `.pipe-`.
- `style.css` — control-plane views, scoped to `#cp-root`.
- `platform/` — the operator/platform-ops console half of the same page; see its README.
