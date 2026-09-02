# app/

Example/demo microservices deployed *through* the control-plane platform to exercise its
build, scan and deploy pipeline. These are not part of the control plane itself — they are
sample FastAPI services that the platform discovers, containerizes, scans with Trivy, and
rolls out to a namespace or a project's own cluster.

Each service directory (`catalog/items`, `inventory-service`, `orders-service`,
`products-service`, `users-service`) exposes the same runtime contract: `GET /livez`,
`GET /readyz` (Vault-reachability aware), `GET /metrics`, and is discovered by the presence
of a `main.py`. They all import shared code from `shared/`.

- `Dockerfile` — single generic multi-stage Dockerfile used to build *any* discovered
  service via `--build-arg SERVICE_NAME=<name>`. Installs `shared/requirements.txt` plus the
  service's own `requirements.txt`, installs the `shared` package itself, strips
  vulnerable vendored `setuptools`/`wheel`/`jaraco.context` copies left by the base image so
  the Trivy scan gate passes, runs as a non-root `appuser`, and defines the `HEALTHCHECK`
  against `/livez`.
- `.dockerignore` — build context is always `app/`; excludes VCS/tooling caches and stale
  per-service `vault_client.py` copies (services must import `shared.vault_client`, not ship
  their own).
- `catalog/`, `inventory-service/`, `orders-service/`, `products-service/`,
  `users-service/` — the individual demo services.
- `shared/` — the common library (Vault client, structured logging, config) installed into
  every service image.
