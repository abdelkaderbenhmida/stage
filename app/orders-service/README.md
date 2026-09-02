# app/orders-service/

`orders-service` — a demo FastAPI service exposing an order-listing endpoint, deployed
through the control plane's pipeline. On startup it loads `DATABASE_URL` and
`PAYMENT_GATEWAY_KEY` from Vault via `shared.vault_client`; in `dev`/`development`/`local`
environments a missing secret falls back to an in-memory SQLite URL and a per-process
ephemeral token (logged as a warning), while in production a missing secret aborts startup
(`SystemExit`). Depends on `app/shared` for logging, config and Vault access.

- `main.py` — FastAPI app with structured logging and Prometheus instrumentation
  (`/metrics`). Endpoints: `GET /` (service info), `GET /livez`, `GET /readyz`
  (Vault-health-gated), and `GET /orders` (returns a static list of orders linking
  `user_id`/`product_id`).
- `requirements.txt` — service-specific dependencies (`fastapi`, `uvicorn`,
  `prometheus-fastapi-instrumentator`, `hvac`).
- `service.yaml` — ArgoCD ApplicationSet discovery marker (`name: orders-service`,
  image `tag`).
