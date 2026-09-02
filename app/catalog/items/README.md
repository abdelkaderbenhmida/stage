# app/catalog/items/

`catalog-items` — a minimal demo FastAPI service deployed through the control plane. It
loads its secrets (`DATABASE_URL`, `JWT_SECRET_KEY`) from Vault via `shared.vault_client` at
startup, falling back to insecure ephemeral/dev values only when `ENVIRONMENT` is
`dev`/`development`/`local`; in any other environment a missing secret aborts startup
(`SystemExit`), matching the fail-closed contract described in `app/shared/vault_client.py`.
It depends on `app/shared` for logging, secret loading and Vault health checks.

- `main.py` — FastAPI app. Sets up structured logging and Prometheus instrumentation
  (`/metrics`), resolves secrets on import, and exposes `GET /` (service info), `GET /livez`
  (always alive), and `GET /readyz` (200/503 based on Vault reachability).
- `requirements.txt` — service-specific dependencies (`fastapi`, `uvicorn`,
  `prometheus-fastapi-instrumentator`, `hvac`); merged with `shared/requirements.txt` when
  the image is built.
- `service.yaml` — ArgoCD ApplicationSet discovery marker; declares the image `name`
  (`catalog-items`) and `tag` used to generate the Application for this service.
