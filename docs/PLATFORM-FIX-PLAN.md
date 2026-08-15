# Platform fix plan — make the automation actually work

> **Handoff document.** Self-contained: every claim below was verified against the live
> cluster on 2026-08-13. Repo root `/home/gadour/Desktop/stage`, branch `secondary`,
> 3-VM libvirt Kubernetes cluster (master-01 `192.168.56.10`, worker-01 `.11`, worker-02 `.12`).

---

## 1. Why this work exists

The platform's stated thesis is:

> *"Zero hardcoded service names. Add `app/<svc>/main.py` and CI, K8s, Vault, Monitoring and ArgoCD all adapt automatically."*

**The thesis is false, and the platform reports success while being false.** Verified:

| Platform claims | Actual |
|---|---|
| `create_service()` returns *"CI will build, ArgoCD will deploy, Vault will provision"* | It writes 2 files and performs none of those. No git operation anywhere. |
| `GET /api/platform` → `status: "ready"` | `catalog` + `catalog-items` have **never** had a ready pod. Other 3 services run 1 of 2 replicas. 5 of 6 ArgoCD Applications are `Degraded`. |
| Vault auto-provisions per-service secrets | `k8s/vault/manifests.yaml:249` seeds `DATABASE_URL=""` (**empty**) and never creates `JWT_SECRET_KEY`. The generated service `SystemExit`s if either is falsy under `ENVIRONMENT=production` (set by the chart) → **every new service CrashLoops by design**. |
| One ArgoCD Application per discovered service | `k8s/argocd/applicationset.yaml` lines 21/28/43 contain the literal `https://github.com/<owner>/<repo>.git`, and **nothing in the repo ever applies that file** (`argocd-install-app.yaml` only covers `k8s/argocd/install`). |
| New builds roll out | Every Application pins the floating `:secondary` tag with no per-commit override, so ArgoCD **never sees a diff** and never redeploys. CI *does* emit `commit-<sha7>` (`ci-cd.yml:293-302`) but only the `workflow_dispatch`-only `deploy` job uses it. |
| Terraform manages the infrastructure | `terraform.tfstate` holds **1 resource** (`libvirt_domain.node["master-01"]`); **3 VMs are running**. `terraform.tfvars` is AWS-shaped in a libvirt project — 7 undeclared vars, `ssh_user="ubuntu"` vs `devops` everywhere else. `.terraform.tfstate.lock.info` stuck since Aug 12 blocks every command. |
| `devops-service-list` drives Vault provisioning | ConfigMap contains 3 services; discovery finds 4. **`catalog-items` was never provisioned in Vault at all.** |

Two root findings:

**(a) The CI/CD pipeline is currently dead.** `k8s/argocd/install/anonymous-access-patch.yaml` is a kustomize
strategic-merge patch (a partial Deployment), but the Lint job runs `kubeconform` over every YAML under `k8s/`
and rejects it with `missing properties: 'selector'`. Lint fails → **Tests, Build & Push, Trivy, Deploy all skip.**
Confirmed on commit `f67b912`. It is the only partial manifest in the tree.

**(b) Every healthy pod runs a hand-built local image.** `orders-service:latest` exists only on the nodes from a
manual `docker build`. Every pod pulling `ghcr.io/<owner>/*:secondary` fails. The GitOps pipeline has
never delivered an image to this cluster.

### Decisions already taken
- **Git flow:** platform creates branch `service/<name>`, commits, pushes, opens a PR via `gh`. Nothing lands on `secondary` unreviewed.
- **Terraform:** go all the way — reconcile state, then real `apply` from the UI, behind honest preflight checks.

### Hard constraint
Host root filesystem `/dev/nvme0n1p5` is **100% full** — 1.2 GB free of 196 GB. A default worker VM needs 20 GB.
~15 GB is reclaimable (docker 8.6 GB, `~/.cache` 5.2 GB) and `disk_size_gb` validation allows ≥ 8, so provisioning
is achievable *after cleanup*, not today. **The UI must refuse with the real reason, never fail halfway through an apply.**

---

## 2. Codebase orientation (reuse these — do not reinvent)

**`controlplane/platform_ops.py`** (~1400 lines) — all logic; `controlplane/api/routers/platform.py` (~330) is thin routes; `controlplane/web/static/platform/app.js` (~1100,
vanilla JS, **no build step**).

| Helper | Purpose |
|---|---|
| `_run(cmd, timeout=KUBECTL_TIMEOUT, input_=None)` | subprocess wrapper → `{ok, stdout, stderr}`, never raises. `KUBECTL_TIMEOUT=6`, `GH_TIMEOUT=25` |
| `_git(args)` | `git -C ROOT …`, swallows errors |
| `_repo_slug()` | → `<owner>/<repo>` |
| `ServiceError(ValueError)` | mutations raise this; `main.py::_guard` maps it to HTTP 400 |
| `_vault_root_token()` / `_es_credentials()` | read + b64decode a k8s Secret — **the pattern for any credential** |
| `SCRIPTS` + `run_script` / `script_output` / `stop_script` / `_script_reader` | **already-built streaming runner**: allowlist + `Popen` + daemon thread + offset polling + ANSI strip. Terraform/Ansible streaming must extend this, not duplicate it |
| `drift_report()` / `repo_declared_objects()` | k8s drift; terraform drift is its sibling — mirror the shape |
| `pods_status` `pod_detail` `pod_events` `pod_metrics` `rollout_history` `service_drilldown` `argocd_apps` `argocd_sync` `ci_runs` `ci_trigger` `alerts_firing` `alert_history` `es_search_logs` `DASHBOARDS`/`open_dashboard` | existing live-cluster reads/actions |

Frontend helpers: `esc` `toast(msg,ok)` `loading()` `api(method,url,body)` `pill(text,cls)` `fmtDur(s)`
`offlineCard(label,err)` `showLogs(title,text)` `closeDetail()` `switchView`.
Operations view = `renderConfig()`, sub-tabs `["ci","argocd","vault","monitoring","run","drift","logs"]` dispatched
in `loadConfigTab(tab)`; `refreshConfigTab()`. `renderRunTab` already implements the 1.2 s offset-polling pattern
(`state.runTimer`, cleared in `switchConfigTab`) — reuse it for any streamed output.

Tests: `tests/test_ui.py`, 21 passing. Run `ENVIRONMENT=dev pytest controlplane/tests`.
Precedent exists for pure-function unit tests (`_parse_top_output`, `_parse_rollout_history`) and YAML-conformance
tests (`test_alertmanager_receiver_is_wired`).

Tooling present and authenticated: `gh` (as `<owner>`; **note its token lacks `read:packages`**),
`terraform` v1.15.7, `ansible-core` 2.21.1, `kubectl`, `helm`, `virsh`.

### Conventions to hold
- No frontend framework, no build step; vanilla JS appended to `app.js`.
- Reads fail soft → `{"reachable": False, "error": …}`. Mutations raise `ServiceError` → `_guard`.
- Destructive actions require explicit UI confirmation (exact wording given per workstream below).
- **Never** return secret values in an API response or to the browser.
- **Never** accept a client-supplied path or command — allowlist everything, as `SCRIPTS` already does.

---

## WS-0 — Unblock the pipeline *(do first; everything else depends on it)*

**One line.** `.github/workflows/ci-cd.yml:126` — add `! -name '*-patch.yaml'` to the `kubeconform` `find`,
establishing the convention that kustomize patches are partial by design. Do **not** add a fake `selector` to the patch.

```
find k8s/ -name '*.yaml' ! -name '*values*.yaml' ! -name 'Chart.yaml' ! -name 'crds.yaml' \
  ! -name '*-patch.yaml' ! -path '*chart/templates*' -print0 | \
  xargs -0 /tmp/kubeconform -strict -ignore-missing-schemas -kubernetes-version 1.28.0 …
```

**Verify:** push, then `gh run view <id> --repo <owner>/<repo> --json jobs` shows Lint green and
**Build & Push Images** running for the first time in this branch's history. Then confirm
`ghcr.io/<owner>/users-service:secondary` resolves.

---

## WS-C — Fix the four broken automation links *(before WS-A; the golden path is theatre without these)*

### C1 — ApplicationSet repoURL + phantom Applications
Two bugs, one fix. The literal `<owner>/<repo>` placeholder; and the `directories` generator matching
`app/catalog` — a *group folder with no `main.py`* — which produced a phantom `catalog` Application deploying a
service that doesn't exist.

Switch to the **`files` generator** over a per-service marker `app/<svc>/service.yaml` containing `{name: <k8s-name>}`:
- parses as YAML (the reason the files generator couldn't use `main.py`)
- excludes group folders naturally → kills the phantom
- keeps **zero hardcoded service names**
- lets the template read `{{ .name }}` directly, deleting the current `splitList` path gymnastics

Generate it in `create_service()`; backfill for the 4 existing services. Substitute the real repoURL.
Add `k8s/argocd/applications/applicationset-app.yaml` (an Application pointing at the ApplicationSet) so something
actually applies it — consistent with the 9 existing app-of-apps files.

*Trade-off to record in the file header:* a second marker file per service; `main.py` remains the discovery
contract for CI and `introspect.py`.

### C2 — Vault seeding
`k8s/vault/manifests.yaml:249` — generate real values instead of `DATABASE_URL=""`, and add the missing
`JWT_SECRET_KEY` (`openssl rand -hex 32`). Keep idempotence (check-then-write) so re-runs don't clobber a real
secret. The Job is `batch/v1` with `ttlSecondsAfterFinished: 300` and is **currently absent** (TTL expired), so
re-applying re-runs it; a forced re-run must `kubectl delete job vault-setup-job --ignore-not-found` then apply.

### C3 — Image tag never rolls out
Add a `services[0].tag` Helm parameter to the ApplicationSet template sourced from the marker file
(`{{ .tag | default "secondary" }}`), and have CI write the successful build's `commit-<sha7>` back into that
service's `service.yaml` — a normal GitOps commit ArgoCD then syncs. This consumes the immutable tag CI already
produces and makes rollout diff-visible. Also review `imagePullPolicy: IfNotPresent`
(`k8s/apps/chart/templates/deployment.yaml:61`) for floating tags.

### C4 — `renderShared` ownership
Every per-service Application sets `renderShared: "false"`, so on the pure-ArgoCD path the shared `ServiceMonitor`
and `apps-read-self` RoleBinding are **never rendered** — they exist today only because they were applied by hand.
Give them an owner: extend `devops-platform-base` (or add a small `shared` Application) rendering the chart with
`renderShared: true` and the full service list. This also clears them from the drift tab's `in-git-not-gitops` bucket.

---

## WS-A — Golden path: actually ship a service

### `controlplane/platform_ops.py`
```python
def ship_service(app_name, svc_name, open_pr=True) -> dict   # MUTATING → ServiceError
#   1. create_service()  (existing; extend to also write service.yaml)
#   2. git checkout -b service/<name>; git add app/<path>; git commit; git push -u origin
#   3. gh pr create --repo <slug> --head service/<name> --title … --body …

def seed_service_secrets(service) -> dict   # MUTATING
#   kubectl exec -n vault deploy/vault -- env VAULT_TOKEN=<tok> vault kv put \
#     secret/devops-platform/<svc> DATABASE_URL=<generated> JWT_SECRET_KEY=<openssl rand -hex 32>
#   NEVER returns the values. Mirrors the existing vault_secrets() exec pattern.

def sync_service_list() -> dict    # rewrite devops-service-list CM from discover_services()
def rerun_vault_setup() -> dict    # delete job (ignore-not-found) + kubectl apply -f k8s/vault/manifests.yaml
def service_pipeline(service) -> dict   # READ, fails soft — the stage tracker
```

`service_pipeline` returns ordered stages, each `{stage, state: ok|pending|failed|blocked, detail}`:

| Stage | Source of truth |
|---|---|
| files on disk | `_iter_service_dirs()` |
| committed & pushed | `git log origin/<branch> --oneline -- app/<path>` |
| PR open | `gh pr list --head service/<name> --json state` |
| CI green | existing `ci_runs()` filtered to the branch |
| image in GHCR | prefer the CI **build job conclusion** — the `gh` token lacks `read:packages`, so a registry HEAD returns 403. **State this limitation in the UI.** |
| Vault secrets present | `vault_secret_metadata(svc)` — both keys must exist **and be non-empty** |
| ArgoCD synced | existing `argocd_apps()` |
| pods ready | existing `pods_status()` → `readyReplicas == desired` |
| serving `/readyz` 200 | `kubectl exec` curl against the Service |

**The feature is naming the first blocking stage.** Today nothing tells you which of 11 steps you're stuck on.

### `controlplane/api/routers/platform.py`
`POST /api/v1/platform/ship/service` (`ShipIn{app, name, open_pr}`) · `POST /api/v1/platform/ship/{service}/secrets` ·
`POST /api/v1/platform/ship/vault/resync` · `GET /api/v1/platform/ship/{service}/pipeline`. Mutations through `_guard`.

### Frontend
Rework the Apps & Services create box into a Ship flow. Render the stage list with `pill()` per stage, highlight
the blocking stage, poll `/pipeline` using the existing `renderRunTab` timer pattern.

### Confirm dialogs (exact wording)
- **Ship:** *"Create branch `service/<name>`, push to origin, and open a PR against `secondary`? This writes to your GitHub repository."*
- **Seed secrets:** *"Generate and write DATABASE_URL and JWT_SECRET_KEY into Vault at `secret/devops-platform/<svc>`? Existing values will be overwritten."*

**Verify:** ship a throwaway service end to end; the tracker must correctly name the blocking stage at each point.
`catalog-items` (absent from `devops-service-list`) must show its Vault stage **failed** before `sync_service_list()`
and **ok** after.

---

## WS-B — Infrastructure control

### Capacity
`cluster_capacity()` — `kubectl get nodes -o json` (allocatable) + `kubectl get pods -A -o json` (requests).
Report cores/RAM used vs allocatable and, from live HPA `maxReplicas` + per-pod requests:
*"room for N more services at current replicas, M if all burst to HPA max."*
Measured today: 6000m allocatable / 3400m requested (57%); ~11586Mi / 4794Mi (41%); all 3 nodes untainted;
5 services × HPA min 2 / max 5, each pod 100m CPU / 128Mi → **13 more services, or 5 if all burst.**
State plainly that **no cluster-autoscaler exists** — on libvirt, capacity is a human action. That is exactly why
this control belongs in the platform.

### IaC drift
`terraform_drift()`, sibling of `drift_report()`: parse `terraform/terraform.tfstate` for `libvirt_domain.node`
instances, compare to `virsh list --all`, and diff `terraform.tfvars` keys against `variables.tf` declarations.
Surfaces today's three findings: 1-of-3 VMs in state, 7 undeclared AWS vars, stale lock.

### State reconciliation
`terraform_reconcile()` — mutating, confirm-gated:
1. remove the stale `.terraform.tfstate.lock.info`
2. replace `terraform.tfvars` with libvirt-correct values matching the **running** cluster:
   `worker_count=2`, `vm_vcpu=2`, `vm_memory_mb=4096`, `disk_size_gb=20`, `ssh_user=devops`,
   `network_cidr=192.168.56.0/24`, `master_name=master-01`
3. `terraform import` every resource `main.tf` declares but state lacks — the 2 worker `libvirt_domain.node`,
   plus per-node `libvirt_volume.node`, `libvirt_volume.cloudinit_iso`,
   plus `libvirt_volume.base` and `libvirt_network.platform`.
   **Generate the import list from `main.tf`'s `for_each` keys, not hardcoded.**
   Volume import IDs are the **full pool key** (`/var/lib/libvirt/images/<name>.qcow2`), not the bare name;
   domains import by name; networks by name.
4. **`terraform plan` must come out empty** before any apply is offered.

**Provider import limits (observed, libvirt 0.9.8 / local 2.9.0):** `libvirt_cloudinit_disk` has no import
support at all, `local_file` only materializes on apply, and the network `dns`/`domain`/`ips` + volume
`create`/`source` attributes are import-blind — imported state can never be byte-identical to config, so the
plan shows `-/+` replacements for them. **Apply is therefore never offered from reconcile** (the diffs would
destroy+recreate live VMs; libvirt also refuses to delete in-use volumes). Reconcile reports these remaining
diffs explicitly instead of claiming empty. A future `libvirt` provider release with full import support is
the upgrade path to a truly empty plan.

### Preflight — run before every apply, refuse with the specific reason
`node_preflight(disk_gb, mem_mb)` checks:
- stale lock present
- `terraform plan` non-empty (state still out of sync)
- host free disk < requested `disk_size_gb` — **fails today**
- host available RAM < `vm_memory_mb`
- `/tmp/kubeadm-join.txt` missing/expired on master — **fails today** (verified absent; regenerate with
  `kubeadm token create --print-join-command`; tokens are 24 h TTL and `/tmp` clears on reboot)

### Add / remove worker
Extend the existing `SCRIPTS` allowlist + `run_script()` machinery with terraform/ansible entries — it already
streams to the UI.
- **add:** bump `worker_count` → `terraform apply` → `scripts/generate-inventory.sh` →
  `ansible-playbook playbook.yml --limit <host> --tags docker,k8s,worker`
  (`ansible/roles/k8s_worker` is a clean idempotent standalone join: slurps the join command from the master,
  asserts it, runs with `args.creates: /etc/kubernetes/kubelet.conf`, polls the node Ready 24×10 s)
- **remove:** `kubectl drain <node> --ignore-daemonsets --delete-emptydir-data` → `kubectl delete node` →
  lower `worker_count` → `terraform apply`. **Order matters** — reversed, it orphans a NotReady node.
- Leave `ansible/roles/k8s_reset` alone — it has an interactive `pause`, not UI-drivable.

**Confirm dialog:** *"Provision worker-03: create a new VM (N GB disk, M MB RAM) and join it to the cluster. This allocates real host resources and takes several minutes."*

**Verify:** reconcile rebuilds state from the live cluster (11 resources: network, base volume, 3 domains,
6 node/cloudinit volumes) and reports the provider-import-gap diffs explicitly; preflight **correctly refuses**
the node-add today citing the full disk — *that refusal is a deliverable*, the platform proving it won't start
work it knows will fail. After freeing space and setting `disk_size_gb=10`, a real `worker-03` joins and
`kubectl get nodes` shows 4.

---

## WS-D — Honest status

`platform_overview()` (`controlplane/platform_ops.py` ~line 655) derives `status: "ready"` purely from `layer_checks`
file-existence booleans. Replace with an outcome roll-up over `service_pipeline()`: `healthy` only when every
service reaches pods-ready; otherwise `degraded` with the count and the first blocking stage. Surface per-service
on Platform Health (`loadHealthBoard`).

**Verify:** against today's cluster the endpoint must report **degraded**, naming `catalog`/`catalog-items` as
never-started — not `ready`.

---

## Order & risk

| # | WS | Why here | Risk |
|---|---|---|---|
| 1 | **WS-0** | One line; unblocks CI, without which nothing can be proven | none |
| 2 | **WS-C** | Makes the existing claim true; WS-A is theatre without it | med — C1 changes the discovery marker, C3 adds CI write-back |
| 3 | **WS-D** | Small; the honest baseline to measure everything else against | low |
| 4 | **WS-A** | Headline feature; needs C + a real green CI | med — writes to git/GitHub and Vault |
| 5 | **WS-B** | Independent; reconcile is safe, apply is gated | high — real VMs; blocked on disk today |

## Verification overall

- `node --check controlplane/web/static/platform/app.js` after every frontend change.
- `ENVIRONMENT=dev pytest controlplane/tests` — 31 pass today. Add cluster-free tests:
  tfvars conformance (every key declared in `variables.tf`), no partial manifests outside `*-patch.yaml`,
  `service_pipeline` stage-transition table, terraform import-command generation, command allowlists.
- End-to-end proof: CI goes green and pushes an image for the first time; a shipped service reaches pods-ready;
  reconcile rebuilds state from the live cluster and reports provider-import-gap diffs (plan empty is blocked
  by libvirt 0.9.8 import limits, documented above); preflight refuses the node-add with the real reason.

---

## Appendix — reproducing the evidence

```bash
# platform claims ready while nothing is fully healthy
curl -s localhost:8099/api/platform | jq '.overview.status'
kubectl get deploy -n devops-platform -o custom-columns=NAME:.metadata.name,READY:.status.readyReplicas,DESIRED:.spec.replicas

# CI dead: Lint fails, Build & Push skipped
gh run list --repo <owner>/<repo> --branch secondary --limit 1
gh run view <id> --repo <owner>/<repo> --json jobs \
  --jq '.jobs[] | "\(.conclusion // .status)\t\(.name)"'

# healthy pods run local images; registry pulls fail
kubectl get pods -n devops-platform \
  -o custom-columns=POD:.metadata.name,READY:.status.containerStatuses[0].ready,IMAGE:.spec.containers[0].image

# Vault provisioning list is stale (3) vs discovered (4)
kubectl get cm devops-service-list -n vault -o jsonpath='{.data.services}'
cd ui && python3 -c "import introspect; print(introspect.discover_services())"

# IaC is fiction: 1 resource in state, 3 VMs running, stale lock
python3 -c "import json;d=json.load(open('terraform/terraform.tfstate'));print(len(d['resources']))"
virsh list --all
ls -la terraform/.terraform.tfstate.lock.info

# host disk full — blocks any new VM
df -h /var/lib/libvirt/images
```
