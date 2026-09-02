# scripts

Operational scripts for the DevOps Central Platform: standing up local dependencies
(Vault, registry, observability), bootstrapping secrets out-of-band so they never touch
git or `ps`, backup/restore, worker-VM scaling, and pre-deploy validation. Every shell
script here is documented in the repo root `README.md`'s scripts table, and
`tests/test_docs_conformance.py::test_every_script_is_documented` fails CI if a new
`.sh`/`.py` file is added here without an entry there.

- `backup.sh` — backs up the database and Terraform workspaces as one timestamped,
  checksummed tarball, so a restore never mixes state from different instants.
- `restore.sh` — restores a `backup.sh` unit; verifies checksum and manifest stamp
  before touching anything, requires `FORCE=1` to overwrite a non-empty workspace root.
- `bootstrap-vault-secret.sh` — creates/rotates the `vault-root-token` Kubernetes Secret
  without ever writing the token to disk, git, or argv.
- `bootstrap-elasticsearch-secret.sh` — same discipline as above, for the Elasticsearch
  and Kibana credential Secrets in the `monitoring` namespace.
- `bootstrap-ghcr-pull.sh` — materializes the GHCR pull-credentials Secret from
  `.env`'s `GHCR_PAT` into the `devops-platform` namespace.
- `generate-inventory.sh` — refreshes the Terraform-rendered Ansible inventory and
  copies it to `ansible/inventory.ini`.
- `render-env.sh` — replaces `__TOKEN__` markers in tracked deploy-config files with
  values from `.env`; idempotent, `--check` reports files still holding tokens.
- `local-vault.sh` — runs Vault in dev mode (in-memory, auto-unsealed) for local work;
  not for anything beyond a laptop.
- `local-registry.sh` — stands up a local image registry reachable from both the host
  (`docker push`) and every kind node (containerd mirror).
- `local-observability.sh` — deploys Loki/promtail if missing and holds port-forwards
  for Prometheus and Loki so the host-side control plane can reach them.
- `platform-worker-add.sh` / `platform-worker-remove.sh` — scale the libvirt cluster by
  one worker VM: bump `worker_count`, `terraform apply`, regenerate inventory, Ansible
  join (add) or drain/delete-node (remove).
- `smoke-test.sh` — E2E sanity check against a live cluster: pod health, service
  endpoints, Prometheus scraping, Grafana datasource.
- `stress-hpa.sh` — load-tests a service with `ab` and verifies HPA scale-up.
- `stress-panel.py` — stdlib-only web UI to start/stop `stress-hpa.sh` and watch live
  HPA/pod/VM status.
- `validate-platform.sh` — the Phase 7 end-to-end validation: cluster health, Trivy,
  Gitleaks, ArgoCD sync, Grafana, Kibana, plus an optional self-healing test.
- `validate-security.sh` — DevSecOps checks: image scanning, secret scanning, Vault
  token validation.
- `hash-requirements.py` — generates `requirements-hashed.txt` offline from pip's wheel
  cache, for build boxes without PyPI access.
- `seed-demo.py` — recreates a populated demo tenancy by driving the real HTTP API.
