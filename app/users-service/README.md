# app/users-service/

`users-service` — a demo FastAPI service exposing a user-listing endpoint, deployed through
the control plane's pipeline. On startup it loads `DATABASE_URL` and `JWT_SECRET_KEY` from
Vault via `shared.vault_client`; in `dev`/`development`/`local` environments a missing secret
falls back to an in-memory SQLite URL and a per-process ephemeral JWT key (logged as a
warning), while in production a missing secret aborts startup (`SystemExit`). Depends on
`app/shared` for logging, config and Vault access.

- `main.py` — FastAPI app with structured logging and Prometheus instrumentation
  (`/metrics`). Endpoints: `GET /` (service info), `GET /livez` (liveness only, no
  dependency checks), `GET /readyz` (Vault-health-gated, so k8s can keep the pod out of
  the endpoints list without killing it), and `GET /users` (returns a static list of
  `{id, name}`).
- `requirements.txt` — service-specific dependencies (`fastapi`, `uvicorn`,
  `prometheus-fastapi-instrumentator`, `hvac`).
- `service.yaml` — ArgoCD ApplicationSet discovery marker (`name: users-service`,
  image `tag`).
