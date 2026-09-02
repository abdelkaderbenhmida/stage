# Tekton — tenant builds as Pods in the tenant's own namespace

Opt-in. `TEKTON_ENABLED=false` by default, and while it is off the deploy path
runs the existing sandbox runner unchanged.

## Why

Today a tenant's build runs in a Docker container on the control-plane host,
alongside every other tenant's build. The isolation is real but it is enforced
by this repository's own code (`controlplane/runners/sandbox.py`): CPU and
memory caps, a wall-clock timeout, no network unless asked, secrets in a 0600
env-file rather than argv.

Under Tekton the build runs as a Pod **in the tenant's own namespace**, so it
inherits the controls the platform already applies to that namespace:

| Existing control | Now also covers builds |
| --- | --- |
| ResourceQuota | a large build cannot starve other tenants |
| default-deny NetworkPolicy | a build cannot reach another tenant's pods |
| dedicated ServiceAccount | a build holds exactly the tenant's permissions |
| namespace derived from project UUID | two teams' `staging` builds cannot collide |

Builds also stop competing with the API for host CPU.

## What changes, and the part that is not free

`docker build` cannot run in a Pod without mounting the Docker socket, and that
socket is root on the node — handing it to a tenant build would undo every
guarantee above. So the build step becomes **kaniko**, which builds an image
from a Dockerfile with no daemon at all.

That has consequences worth stating before anyone flips the flag:

- **Registry credentials move.** The sandbox writes them to a private env-file
  and deletes it after the run. Kaniko wants a `config.json` in a Secret. The
  guarantee to re-prove is that the value never lands in a container's argv or
  in an image layer.
- **`SANDBOX_*` limits stop applying.** They are docker flags. The equivalent
  is the ResourceQuota plus the `resources` block in the Task, and a Pipeline
  `timeout` in place of the wall-clock kill.
- **Kaniko is not a drop-in for every Dockerfile.** It resolves `RUN` in
  userspace; images relying on daemon-specific behaviour can need adjusting.

## Install

Tekton itself is upstream and is not vendored here:

```bash
kubectl apply -f https://storage.googleapis.com/tekton-releases/pipeline/latest/release.yaml
kubectl -n tekton-pipelines rollout status deployment/tekton-pipelines-controller
```

Then the platform's Pipeline, which is installed **once per tenant namespace**
by the control plane rather than cluster-wide — a tenant must not be able to
reference another tenant's Pipeline object:

```bash
kubectl apply -n <project-namespace> -f k8s/tekton/pipeline.yaml
```

## Dashboard

`controlplane/workers/tasks.py:_install_tenant_dashboard` applies
`k8s/tekton/dashboard.yaml` into every tenant namespace automatically, on
every provision, the same way it applies `pipeline.yaml`. It is a real,
read-only copy of the upstream Tekton Dashboard — not the platform console's
own pipeline graph — scoped so one tenant cannot see another's builds:

- one Deployment per namespace, not one shared instance, because the
  Dashboard backend has no per-request RBAC — it shows whatever its
  ServiceAccount can see, cluster-wide by default
- that ServiceAccount's Role only grants `get/list/watch` on Tekton and pod
  objects **inside its own namespace**
- the container itself also runs with `--read-only=true` and
  `--namespace=$(POD_NAMESPACE)` (downward API) as a second layer

Nothing exposes it outside the cluster by default — only a `ClusterIP`
Service named `tekton-dashboard` in the tenant's namespace. Reach it with:

```bash
kubectl -n <project-namespace> port-forward svc/tekton-dashboard 9097:9097
```

Pin `dashboard.yaml`'s image tag to a Dashboard release that matches the
Tekton Pipelines version from the `latest/release.yaml` install above —
check https://github.com/tektoncd/dashboard/releases for the pairing before
relying on this in a real cluster.

## The contract with the graph

`controlplane/core/tekton_status.py` maps a PipelineRun and its TaskRuns onto
the same six-value vocabulary `job_steps` already uses, so the pipeline graph
in the console keeps working without a renderer change. The task names in
`pipeline.yaml` are the labels the tenant sees, and they are asserted against
`TEKTON_PIPELINE_TASKS` in `controlplane/runners/tekton.py` — renaming one here
without renaming it there fails the test rather than silently drawing a graph
with a gap in it.
