# Devops Platform UI

Local dashboard that introspects the repo (no cluster access) and visualises
everything the discovery-driven platform auto-derives from `app/*/main.py`.

## Run

```bash
pip install -r ui/requirements.txt
uvicorn ui.main:app --port 8080 --app-dir ui

# or, from repo root:
PYTHONPATH=ui uvicorn ui.main:app --port 8080
```

Open http://127.0.0.1:8080

## Views

| View | What it shows |
|---|---|
| Overview | Platform identity (branch/commit), layer checks, discovered services |
| Services | Every `app/*/main.py` service: title, version, endpoints, vault usage |
| Helm | Live `helm template` render: object kind counts, per-service matrix, shared objects |
| CI/CD | Pipeline DAG, triggers, dynamic `fromJSON` matrices, permissions |
| Vault | ConfigMap-driven setup loop (policies + k8s-auth roles per service), manifest objects |
| Monitoring | Single ServiceMonitor `part-of` matcher, SLO rules/targets |
| ArgoCD | ApplicationSet git `files` generator, sync policy, static applications |

## API

- `GET /api/platform` — all sections in one call
- `GET /api/overview|services|helm|ci|vault|monitoring|argocd` — per section
- `GET /api/health` — liveness

## Notes

- Lives in `ui/` (NOT `app/`) so discovery, Helm rendering, CI and ArgoCD stay
  untouched — adding the UI adds zero platform config.
- Requires `helm` on PATH for the Helm view (others are pure file reads).
