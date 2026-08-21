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
        _taskrun("build", "False", "Failed"),
    ]
    steps = pipeline_steps(_run("False", "Failed"), taskruns, tekton.TEKTON_PIPELINE_TASKS)

    assert [(s.name, s.status) for s in steps] == [
        ("clone", "succeeded"), ("build", "failed"), ("scan", "skipped"),
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


def test_step_indices_line_up_with_the_builtin_seven_when_there_are_no_stages():
    """The graph unions job_steps rows with the deploy template. Numbering the
    three tasks 1..3 would put "scan" where the template says "push"."""
    from controlplane.workers.steps import JOB_STEP_TEMPLATES

    template = JOB_STEP_TEMPLATES["deploy"]
    indices = tekton.step_indices([])

    assert indices == {"clone": 1, "build": 2, "scan": 4}
    assert "clon" in template[indices["clone"] - 1]
    assert "build" in template[indices["build"] - 1]
    assert "scan" in template[indices["scan"] - 1]
    assert "push" in template[tekton.push_step_index([]) - 1]


def test_tenant_stages_shift_the_builtin_steps():
    """Stages run between clone and build, exactly as on the sandbox path, so
    a two-stage repository pushes build/scan two places along."""
    stages = [_Stage("unit tests"), _Stage("lint")]
    indices = tekton.step_indices(stages)

    assert indices["clone"] == 1
    assert indices[tekton.stage_task_name(1, "unit tests")] == 2
    assert indices[tekton.stage_task_name(2, "lint")] == 3
    assert indices["build"] == 4
    assert tekton.push_step_index(stages) == 5
    assert indices["scan"] == 6


def test_stages_become_tasks_between_clone_and_build():
    """A failing test must stop the pipeline before it spends a build slot."""
    spec = tekton.build_pipeline_spec([_Stage("unit tests"), _Stage("lint")], "sandbox:latest")
    order = [t["name"] for t in spec["tasks"]]

    assert order[0] == "clone"
    assert order[-2:] == ["build", "scan"]
    assert len(order) == 5
    # Each stage runs after the previous one, so they are sequential rather
    # than a fan-out that would let a later stage start before an earlier
    # one failed.
    build = next(t for t in spec["tasks"] if t["name"] == "build")
    assert build["runAfter"] == [order[2]]


def test_stage_names_are_sanitised_but_the_tenants_wording_is_kept():
    """Tekton object names are DNS-1123 labels; "unit tests" is not one. The
    tenant must still see their own wording in the graph."""
    spec = tekton.build_pipeline_spec([_Stage("Unit Tests & Lint!")], "sandbox:latest")
    task = spec["tasks"][1]

    assert task["name"] == tekton.stage_task_name(1, "Unit Tests & Lint!")
    assert task["name"].replace("-", "").isalnum()
    assert task["taskSpec"]["metadata"]["labels"]["controlplane.io/stage-name"] == "Unit Tests & Lint!"


def test_two_stages_with_the_same_name_do_not_collide():
    """Tekton rejects a pipeline with duplicate task names outright, with an
    error that names neither stage."""
    spec = tekton.build_pipeline_spec([_Stage("test"), _Stage("test")], "sandbox:latest")
    names = [t["name"] for t in spec["tasks"]]

    assert len(names) == len(set(names))


def test_a_stage_runs_in_the_image_it_asked_for():
    spec = tekton.build_pipeline_spec([_Stage("unit tests", image="python:3.11-slim")], "sandbox:latest")
    step = spec["tasks"][1]["taskSpec"]["steps"][0]

    assert step["image"] == "python:3.11-slim"


def test_a_stage_without_an_image_falls_back_to_the_sandbox_image():
    spec = tekton.build_pipeline_spec([_Stage("unit tests")], "sandbox:latest")

    assert spec["tasks"][1]["taskSpec"]["steps"][0]["image"] == "sandbox:latest"


def test_generated_dockerfile_travels_as_a_parameter():
    """Under Tekton the checkout only exists in the cluster, so a Dockerfile
    written on the control-plane host would never reach kaniko."""
    obj = tekton.render_pipelinerun(
        uuid.uuid4(), uuid.uuid4(), "u", "main", "img", "sa", dockerfile="FROM python:3.12\n",
    )
    params = {p["name"]: p["value"] for p in obj["spec"]["params"]}

    assert params["dockerfile"] == "FROM python:3.12\n"


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
    """Defaulting to the built-in three would drop every tenant stage out of
    the graph."""
    stages = [_Stage("unit tests")]

    assert tekton.pipeline_task_names(stages) == [
        "clone", tekton.stage_task_name(1, "unit tests"), "build", "scan",
    ]
