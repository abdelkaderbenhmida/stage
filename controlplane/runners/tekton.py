"""Run a tenant's build as a Tekton PipelineRun in the tenant's own namespace.

Opt-in (``TEKTON_ENABLED``). While it is off, nothing here is reached and the
sandbox runner in ``runners/sandbox.py`` remains the only build path.

What this module is careful about:

* **It never names a namespace it was handed.** The namespace is derived from
  the project id by ``k8s_namespace``, the same function the rest of the
  platform uses, so a PipelineRun cannot be steered into another tenant's
  namespace by anything a caller supplies.
* **It does not enforce the vulnerability gate itself.** The gate lives in the
  scan Task, which fails closed, and the caller re-checks the run's outcome.
  A gate implemented on the client side of a poll loop is a gate that a lost
  connection turns off.
* **The credential reaches kaniko as a Secret, never as an argument.** Same
  reason the sandbox uses an env-file: a value in argv is readable by anyone
  who can list processes.
"""

from __future__ import annotations

import base64
import ipaddress
import json
import re
import socket
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from controlplane.core.config import settings
from controlplane.core.tekton_status import StepState, condition_status, pipeline_steps
from controlplane.core.validation import k8s_namespace

# The built-in task names, in order, as declared in k8s/tekton/pipeline.yaml.
# The tenant sees these as step labels. Renaming one there without renaming it
# here draws a graph with a permanently-pending box in it, so a test asserts
# the two agree.
TEKTON_PIPELINE_TASKS = ["clone", "secret-scan", "dependency-scan", "build", "scan"]

PIPELINE_NAME = "tenant-deploy"

# Tekton object names are DNS-1123 labels. A stage name is free text out of a
# tenant's .platform.yml ("unit tests", "lint & typecheck"), so it cannot be
# used as a task name as written — but the tenant must still see their own
# wording in the graph, so the readable name is carried in a label and only
# the object name is sanitised.
_NAME_SAFE = re.compile(r"[^a-z0-9-]+")


def stage_task_name(index: int, stage_name: str) -> str:
    """A valid, unique Tekton task name for one tenant-declared stage.

    Prefixed with the index rather than deduplicated by suffix: two stages may
    legitimately share a name, and a pipeline with two tasks called `stage-lint`
    is rejected outright by Tekton with an error that names neither stage.
    """
    slug = _NAME_SAFE.sub("-", stage_name.strip().lower()).strip("-")
    return f"stage-{index}-{slug}"[:57].rstrip("-") or f"stage-{index}"


class TektonError(RuntimeError):
    """The run could not be created, or could not be read back."""


@dataclass(frozen=True)
class PipelineRunResult:
    name: str
    status: str
    steps: list[StepState]
    message: str = ""


def _run_name(deployment_id: uuid.UUID) -> str:
    """Unique per run. Tekton will not overwrite an existing PipelineRun, so a
    fixed name makes every deploy after the first fail with AlreadyExists."""
    return f"deploy-{deployment_id.hex[:12]}-{uuid.uuid4().hex[:6]}"


def _stage_task(index: int, stage, default_image: str) -> dict:
    """One tenant-declared stage as an embedded Tekton task.

    Embedded rather than referenced: a Task object per stage would have to be
    created in the tenant's namespace before the run and cleaned up after it,
    and a failed cleanup would leave a tenant's namespace accumulating objects
    named after every stage they ever ran.
    """
    return {
        "name": stage_task_name(index, stage.name),
        # runAfter is set by the caller, which is the only place that knows
        # what ran before this stage.
        "taskSpec": {
            # An annotation, not a label. Label VALUES must be DNS-safe, and a
            # stage name is free text out of a tenant's .platform.yml — a
            # stage called "unit tests" makes Kubernetes reject the TaskRun
            # outright, so the whole pipeline dies at the first stage with an
            # error about label syntax rather than anything the tenant wrote.
            # Annotations have no such restriction.
            "metadata": {"annotations": {"controlplane.io/stage-name": stage.name[:253]}},
            "workspaces": [{"name": "source"}],
            "steps": [
                {
                    "name": "run",
                    # The tenant chose this image. It runs with the tenant's
                    # own ServiceAccount in the tenant's own namespace, bound
                    # by their ResourceQuota — which is the entire reason this
                    # is safe to allow at all.
                    "image": stage.image or default_image,
                    "workingDir": "$(workspaces.source.path)/repo",
                    "script": "#!/bin/sh\nset -eu\n" + stage.run,
                    # Explicit and modest. Without these the namespace's
                    # LimitRange default applies — 500m of *limit* per step —
                    # and a tenant whose app already uses most of its quota
                    # cannot run a stage at all: the pod is refused with
                    # "exceeded quota" before the stage's own command is ever
                    # reached. A shell command does not need half a core.
                    "computeResources": {
                        "requests": {"cpu": "50m", "memory": "128Mi"},
                        "limits": {"cpu": "250m", "memory": "512Mi"},
                    },
                }
            ],
        },
        "workspaces": [{"name": "source", "workspace": "source"}],
    }


def build_pipeline_spec(stages: list, default_image: str) -> dict:
    """The inline Pipeline for one deploy: clone, tenant stages, build, scan.

    Built per run rather than referencing the installed Pipeline, because the
    tenant's stages are only known after their repository has been read — a
    static Pipeline cannot express "and then whatever this repo declares".
    The installed k8s/tekton/pipeline.yaml remains the reference definition of
    the three built-in tasks.

    Stages run between clone and build, matching the sandbox path exactly: a
    failing test must stop the pipeline before it spends a build slot, not
    after.
    """
    tasks: list[dict] = [
        {
            "name": "clone",
            "taskRef": {"name": "clone"},
            "params": [
                {"name": "repo-url", "value": "$(params.repo-url)"},
                {"name": "revision", "value": "$(params.revision)"},
                {"name": "dockerfile-b64", "value": "$(params.dockerfile-b64)"},
            ],
            "workspaces": [{"name": "source", "workspace": "source"}],
        },
        # Same two tools the platform's own CI gates on
        # (.github/workflows/ci-cd.yml: gitleaks blocks build, pip-audit
        # --strict fails on known-vulnerable pinned deps), run here as Pods
        # in the tenant's own namespace — not on the control-plane host —
        # before a build slot is spent on a checkout that should never have
        # shipped.
        {
            "name": "secret-scan",
            "taskRef": {"name": "secret-scan"},
            "runAfter": ["clone"],
            "workspaces": [{"name": "source", "workspace": "source"}],
        },
        {
            "name": "dependency-scan",
            "taskRef": {"name": "dependency-scan"},
            "runAfter": ["secret-scan"],
            "workspaces": [{"name": "source", "workspace": "source"}],
        },
    ]

    previous = "dependency-scan"
    for index, stage in enumerate(stages, start=1):
        task = _stage_task(index, stage, default_image)
        task["runAfter"] = [previous]
        tasks.append(task)
        previous = task["name"]

    tasks.append({
        "name": "build",
        "taskRef": {"name": "build"},
        "runAfter": [previous],
        "params": [
            {"name": "image", "value": "$(params.image)"},
            {"name": "registry-insecure", "value": "$(params.registry-insecure)"},
        ],
        "workspaces": [
            {"name": "source", "workspace": "source"},
            {"name": "docker-credentials", "workspace": "docker-credentials"},
        ],
    })
    tasks.append({
        "name": "scan",
        "taskRef": {"name": "scan"},
        "runAfter": ["build"],
        "params": [
            {"name": "image", "value": "$(params.image)"},
            {"name": "registry-insecure", "value": "$(params.registry-insecure)"},
        ],
        "workspaces": [{"name": "source", "workspace": "source"}],
    })

    return {
        "params": [
            {"name": "repo-url", "type": "string"},
            {"name": "revision", "type": "string"},
            {"name": "image", "type": "string"},
            {"name": "registry-insecure", "type": "string", "default": "false"},
            # Empty unless the platform generated one, and base64: under
            # Tekton the checkout only exists inside the cluster, so a
            # Dockerfile written on the control-plane host never reaches
            # kaniko — and Tekton substitutes params textually into the task's
            # shell script, where a Dockerfile's own quotes and newlines would
            # be mangled (or, for a tenant-supplied value, executed).
            {"name": "dockerfile-b64", "type": "string", "default": ""},
        ],
        "workspaces": [{"name": "source"}, {"name": "docker-credentials"}],
        "tasks": tasks,
    }


def pipeline_task_names(stages: list) -> list[str]:
    """Every task in the built pipeline, in order — the labels the tenant sees."""
    names = ["clone", "secret-scan", "dependency-scan"]
    for index, stage in enumerate(stages, start=1):
        names.append(stage_task_name(index, stage.name))
    return [*names, "build", "scan"]


def _b64(text: str) -> str:
    """Base64 for a value that has to survive textual substitution into a shell.

    The base64 alphabet has no quotes, no whitespace and no shell
    metacharacters, so a value encoded here cannot change the meaning of the
    script it is pasted into, whatever it contained.
    """
    return base64.b64encode(text.encode()).decode() if text else ""


def registry_egress_warning(registry: str, registry_cidr: str) -> str:
    """A warning when a build will not be able to reach the registry, or "".

    The tenant namespace's default-deny egress excludes RFC1918, so a registry
    in private space is unreachable from a build pod unless REGISTRY_CIDR
    opened it. Without this the pipeline resolves the registry, hangs on the
    push, and fails on the wall-clock timeout — thirty minutes later, naming a
    connection to an address the tenant never configured.

    A warning rather than a refusal: the registry may legitimately be public,
    and DNS may resolve to something this cannot see from here.
    """
    if registry_cidr:
        return ""

    host = registry.split("/", 1)[0].rsplit(":", 1)[0].strip("[]")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        try:
            address = ipaddress.ip_address(socket.gethostbyname(host))
        except (OSError, ValueError):
            return ""

    # Loopback and link-local are private by ipaddress's reckoning, and both
    # are a different mistake: a build pod resolving them reaches itself and
    # the node's metadata endpoint respectively. Opening REGISTRY_CIDR would
    # not help, so suggesting it would send the reader the wrong way.
    if address.is_loopback or address.is_link_local:
        return (
            f"WARNING: the registry {registry} resolves to {address}, which inside a build "
            "pod means the pod itself. Set REGISTRY_INTERNAL to the address the registry "
            "answers on from within the cluster."
        )
    if not address.is_private:
        return ""
    return (
        f"WARNING: the registry {registry} resolves to {address}, which is inside the "
        "range a tenant namespace's default-deny egress blocks. Set REGISTRY_CIDR "
        f"(for example {address}/32) or this build will time out on the push."
    )


def step_indices(stages: list) -> dict[str, int]:
    """Where each pipeline task lands in the deploy job's step numbering.

    The graph unions job_steps rows with the nine-step deploy template
    (workers/steps.py), and the sandbox path inserts tenant stages after its
    own fixed prefix of three — clone, secret scan gate, dependency scan gate
    (core/pipeline_graph.py:_DEPLOY_FIXED_PREFIX) — so the built-in steps
    after that shift by however many stages there are. Numbering the Tekton
    tasks 1..n instead would put "scan" where the template says "push" and
    leave phantom boxes the tenant never ran.

    There is no entry for "push": kaniko builds and pushes in one task, so that
    step is recorded from the build's own success.
    """
    indices = {
        "clone": 1,
        "secret-scan": 2,
        "dependency-scan": 3,
        "build": 4 + len(stages),
        "scan": 6 + len(stages),
    }
    for offset, stage in enumerate(stages, start=1):
        indices[stage_task_name(offset, stage.name)] = 3 + offset
    return indices


def push_step_index(stages: list) -> int:
    """Step 5 of the built-in nine, shifted past the tenant's own stages."""
    return 5 + len(stages)


def render_pipelinerun(
    project_id: uuid.UUID,
    deployment_id: uuid.UUID,
    repo_url: str,
    revision: str,
    image: str,
    service_account: str,
    timeout: str = "30m",
    stages: list | None = None,
    dockerfile: str = "",
) -> dict:
    """The PipelineRun object for one deploy."""
    stages = stages or []
    return {
        "apiVersion": "tekton.dev/v1",
        "kind": "PipelineRun",
        "metadata": {
            "name": _run_name(deployment_id),
            # Derived, never passed in — this is the tenancy boundary.
            "namespace": k8s_namespace(project_id),
            "labels": {
                "app.kubernetes.io/managed-by": "controlplane",
                "controlplane.io/deployment": deployment_id.hex[:20],
            },
        },
        "spec": {
            "pipelineSpec": build_pipeline_spec(stages, settings.sandbox_image),
            # The tenant's own ServiceAccount, so the build holds exactly the
            # permissions the tenant has and nothing the control plane has.
            "taskRunTemplate": {"serviceAccountName": service_account},
            # Replaces the sandbox's wall-clock kill, which was a docker flag
            # and does not apply here. Without it a wedged build holds a
            # workspace volume indefinitely.
            "timeouts": {"pipeline": timeout},
            "params": [
                {"name": "repo-url", "value": repo_url},
                {"name": "revision", "value": revision},
                {"name": "image", "value": image},
                {"name": "registry-insecure", "value": "true" if settings.registry_insecure else "false"},
                {"name": "dockerfile-b64", "value": _b64(dockerfile)},
            ],
            "workspaces": [
                {
                    "name": "source",
                    "volumeClaimTemplate": {
                        "spec": {
                            "accessModes": ["ReadWriteOnce"],
                            "resources": {"requests": {"storage": "2Gi"}},
                        }
                    },
                },
                {"name": "docker-credentials", "secret": {"secretName": "registry-credentials"}},
            ],
        },
    }


@dataclass
class KubectlCaller:
    """How this module reaches the cluster.

    Injected rather than imported so the poll loop can be tested without a
    cluster: a test supplies a callable returning recorded API objects.
    """

    call: object
    # Callable[[dict], None]. Optional because ``call`` alone is enough for
    # every read (get pipelinerun/taskrun) — only start() needs to hand the
    # cluster a manifest, and where ``call`` actually runs determines how
    # that manifest has to get there. The default below assumes ``call``
    # can see a host tempfile directly, which is true for a fake in tests
    # but false for a real kubectl running inside a sandbox container — the
    # real caller (workers/tasks.py:_tekton_kubectl) supplies its own that
    # mounts the manifest in first.
    apply_manifest: object = None

    def json(self, args: list[str]) -> dict:
        raw = self.call(args)
        try:
            return json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise TektonError(f"could not read kubectl output: {str(raw)[:200]}") from exc

    def apply(self, manifest: dict) -> None:
        if self.apply_manifest is not None:
            self.apply_manifest(manifest)
            return
        with TemporaryDirectory(prefix="ctl-tekton-") as tmp:
            path = Path(tmp) / "pipelinerun.json"
            path.write_text(json.dumps(manifest))
            self.call(["apply", "-f", str(path)])


def start(kubectl: KubectlCaller, pipelinerun: dict) -> str:
    """Create the PipelineRun. Returns its name."""
    kubectl.apply(pipelinerun)
    return pipelinerun["metadata"]["name"]


def read(kubectl: KubectlCaller, namespace: str, name: str, declared: list[str] | None = None) -> PipelineRunResult:
    """One snapshot of a run: its overall status and every declared step.

    ``declared`` is the task list of the pipeline that was actually submitted —
    which includes the tenant's own stages, so it is not TEKTON_PIPELINE_TASKS
    whenever a repository declares any. Defaulting to the built-in three would
    drop every tenant stage out of the graph.
    """
    run = kubectl.json(["get", "pipelinerun", name, f"--namespace={namespace}", "-o", "json"])
    taskruns = kubectl.json([
        "get", "taskrun", f"--namespace={namespace}",
        # Tekton's own label, so this finds the TaskRuns of this run only —
        # listing the namespace unfiltered would mix in a concurrent deploy of
        # a different service and attribute its steps to this one.
        "-l", f"tekton.dev/pipelineRun={name}",
        "-o", "json",
    ]).get("items", [])

    status = condition_status(run)
    conditions = (run.get("status") or {}).get("conditions") or []
    message = next((c.get("message", "") for c in conditions if c.get("type") == "Succeeded"), "")
    return PipelineRunResult(
        name=name,
        status=status,
        steps=pipeline_steps(run, taskruns, declared or TEKTON_PIPELINE_TASKS),
        message=message,
    )


def wait(
    kubectl: KubectlCaller,
    namespace: str,
    name: str,
    on_state=None,
    declared: list[str] | None = None,
    poll_seconds: float = 5.0,
    timeout_seconds: int = 2400,
    sleep=time.sleep,
) -> PipelineRunResult:
    """Poll until the run reaches a terminal state, or the deadline passes.

    ``on_state`` is called with every snapshot, which is how the job's steps
    and log stay live in the console while the run is in flight.

    The deadline is deliberately longer than the Pipeline's own timeout: Tekton
    is the authority on when a run is over, and a client that gave up first
    would report a failure for a run that then went on to succeed, leaving the
    tenant's deployment row disagreeing with their cluster.
    """
    deadline = time.monotonic() + timeout_seconds
    last: PipelineRunResult | None = None
    while True:
        last = read(kubectl, namespace, name, declared)
        if on_state is not None:
            on_state(last)
        if last.status in ("succeeded", "failed", "cancelled"):
            return last
        if time.monotonic() >= deadline:
            raise TektonError(
                f"timed out after {timeout_seconds}s waiting for PipelineRun {name}; "
                f"last status was {last.status}."
            )
        sleep(poll_seconds)
