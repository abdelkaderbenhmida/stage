# app/inventory-service/

`inventory-service` — a demo FastAPI service exposing a stock-listing endpoint, deployed
through the control plane's build/scan/deploy pipeline. On startup it loads `DATABASE_URL`
and `STOCK_SYNC_KEY` from Vault via `shared.vault_client`; in `dev`/`development`/`local`
environments missing secrets fall back to an in-memory SQLite URL and a per-process ephemeral
token (logged as a warning), while in production a missing secret aborts startup. Depends on
`app/shared` for logging, config and Vault access.

- `main.py` — FastAPI app with structured logging and Prometheus instrumentation
  (`/metrics`). Endpoints: `GET /` (service info), `GET /livez`, `GET /readyz`
  (Vault-health-gated), and `GET /inventory` (returns a static list of `{product_id, stock}`).
- `requirements.txt` — service-specific dependencies (`fastapi`, `uvicorn`,
  `prometheus-fastapi-instrumentator`, `hvac`).
- `service.yaml` — ArgoCD ApplicationSet discovery marker (`name: inventory-service`,
  image `tag`).
