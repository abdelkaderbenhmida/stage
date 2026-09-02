# web

Serves the single-page web UI from the API process.

- `router.py` — one route, `GET /`, returning `static/index.html` with
  `Cache-Control: no-cache, must-revalidate`. Client-side hash routing handles every
  view, including the operator console under `#/platform/*`, so this is the only HTML
  entrypoint. The `no-cache` header matters because response headers (including the CSP
  governing whether the operator console may embed a dashboard) travel with this
  document — a stale cached shell means a stale CSP.
- `static/` — the UI itself; see its README.
