"""Celery task logic with runners stubbed — exercises the orchestration,
gate logic and error handling without touching real infra (docs/PLATFORM_SPEC.md
§7, phases F1-F7)."""

import json

import pytest
import sqlalchemy as sa
from controlplane.core.security import hash_password
from controlplane.models import Deployment, Job, Project, Scan, User
from controlplane.runners.scanners.base import RawResult
from controlplane.workers import tasks

pytestmark = pytest.mark.integration

SPEC = {
    "version": 1,
    "project": "my-cluster",
    "network": {"cidr": "192.168.56.0/24", "domain": "devops.local"},
    "nodes": [
        {"name": "master", "vcpu": 4, "memory_mb": 8192, "disk_gb": 50, "role": "k8s_master"},
        {"name": "worker-1", "vcpu": 2, "memory_mb": 4096, "disk_gb": 30, "role": "k8s_worker"},
    ],
}

TRIVY_OK = json.dumps({"Results": []})
TRIVY_BLOCKED = json.dumps(
    {
        "Results": [
            {"Target": "img", "Vulnerabilities": [
                {"VulnerabilityID": "CVE-2024-1", "Severity": "CRITICAL", "PkgName": "x", "Title": "t"}
            ]}
        ]
    }
)


@pytest.fixture()
def user(session):
    user = User(email="alice@example.com", password_hash=hash_password("Sup3rSecret!"), role="user")
    session.add(user)
    session.commit()
    return user


@pytest.fixture()
def team(session, user):
    from controlplane.repositories.teams import ensure_personal_team

    return ensure_personal_team(session, user)


@pytest.fixture()
def project(session, user, team):
    project = Project(owner_id=user.id, team_id=team.id, name="my-cluster", infra_spec=SPEC, status="draft")
    session.add(project)
    session.flush()
    # Nodes are normally created alongside the project by POST /projects;
    # provision_task only fills in their IPs, so they must already exist.
    from controlplane.models import Node

    for node in SPEC["nodes"]:
        session.add(
            Node(
                project_id=project.id,
                name=node["name"],
                vcpu=node["vcpu"],
                memory_mb=node["memory_mb"],
                disk_gb=node["disk_gb"],
                role=node["role"],
            )
        )
    session.commit()
    return project


class _StubResult:
    def __init__(self, exit_code=0, output="", timed_out=False):
        self.exit_code = exit_code
        self.output = output
        self.timed_out = timed_out


@pytest.fixture()
def stub_runners(monkeypatch):
    """Replace every runner the tasks touch with deterministic stubs."""

    def _ok(*args, **kwargs):
        return _StubResult(exit_code=0, output="ok")

    monkeypatch.setattr(tasks, "render_terraform", lambda *a, **k: [])
    monkeypatch.setattr(tasks, "render_ansible", lambda *a, **k: [])
    monkeypatch.setattr(tasks, "terraform_init", _ok)
    monkeypatch.setattr(tasks, "terraform_apply", _ok)
    monkeypatch.setattr(tasks, "terraform_output", lambda *a, **k: _StubResult(exit_code=0, output='{"master": "192.168.56.10", "worker-1": "192.168.56.11"}'))
    monkeypatch.setattr(tasks, "terraform_destroy", _ok)
    monkeypatch.setattr(tasks, "ansible_playbook", _ok)
    monkeypatch.setattr(tasks, "user_ssh_private_key", lambda *a, **k: "dummy-key")
    monkeypatch.setattr(tasks, "run_trivy", lambda *a, **k: RawResult(tool="trivy", target="img", stdout=TRIVY_OK))
    monkeypatch.setattr(tasks, "run_gitleaks", lambda *a, **k: RawResult(tool="gitleaks", target="x"))
    monkeypatch.setattr(tasks, "run_pip_audit", lambda *a, **k: RawResult(tool="pip_audit", target="x"))
    # A stand-in checkout that looks like something deployable. deploy_task
    # now refuses to spend a build slot on a repository with no Dockerfile at
    # its root, so a bare path would fail there rather than at the gate and
    # rollout logic these tests are about.
    def _fake_clone(*a, **k):
        import pathlib
        import tempfile

        repo = pathlib.Path(tempfile.mkdtemp(prefix="fake-repo-")) / "repo"
        repo.mkdir(parents=True, exist_ok=True)
        (repo / "Dockerfile").write_text("FROM scratch\n")
        return repo

    monkeypatch.setattr(tasks, "_clone_repo", _fake_clone)
    # deploy_task shells out through run_sandbox for `docker build` and
    # `docker push`; without stubbing it the task fails long before reaching
    # the trivy gate and rollout logic these tests are actually asserting on.
    monkeypatch.setattr(tasks, "run_sandbox", _ok)
    return tasks


def _new_job(session, project, job_type):
    job = Job(project_id=project.id, type=job_type, status="queued")
    session.add(job)
    session.commit()
    return job


def test_provision_task_happy_path(session, project, user, stub_runners):
    job = _new_job(session, project, "provision")
    stub_runners.provision_task(str(job.id), str(project.id), str(user.id), SPEC, "/tmp/ws")

    session.refresh(job)
    session.refresh(project)
    assert job.status == "succeeded"
    assert project.status == "ready"
    # node IPs captured and recorded
    from controlplane.models import Node

    ips = {n.name: n.ip_address for n in session.query(Node).filter(Node.project_id == project.id)}
    assert ips["master"] == "192.168.56.10"
    assert ips["worker-1"] == "192.168.56.11"


def test_provision_apply_failure_attempts_cleanup(session, project, user, stub_runners, monkeypatch):
    calls = []

    def _failing_apply(*a, **k):
        calls.append("apply")
        raise RuntimeError("libvirt connect failed")

    monkeypatch.setattr(tasks, "terraform_apply", _failing_apply)
    job = _new_job(session, project, "provision")
    stub_runners.provision_task(str(job.id), str(project.id), str(user.id), SPEC, "/tmp/ws")

    session.refresh(job)
    session.refresh(project)
    assert job.status == "failed"
    assert project.status == "failed"
    assert "apply" in calls
    # cleanup attempted
    assert "cleanup" in job.log.lower() or "destroy" in job.log.lower()


def test_scan_task_persists_findings(session, project, user, stub_runners):
    scan = Scan(project_id=project.id, tool="trivy", target="users-service:1.0.0", status="queued")
    session.add(scan)
    session.commit()
    job = _new_job(session, project, "scan")

    stub_runners.scan_task(str(job.id), str(scan.id), str(project.id), "trivy", "users-service:1.0.0")

    session.refresh(job)
    session.refresh(scan)
    assert job.status == "succeeded"
    assert scan.status == "completed"
    assert scan.summary == {"critical": 0, "high": 0, "medium": 0, "low": 0, "unknown": 0}


@pytest.mark.parametrize("tool", ["gitleaks", "pip_audit"])
def test_scan_task_fails_closed_when_the_scanner_errors(
    session, project, user, stub_runners, monkeypatch, tool
):
    """A scanner that crashed is not a target known to be clean.

    Both of these produce no findings when they fail: gitleaks writes no
    report (read as "[]") and pip-audit writes no JSON (parsed to an empty
    summary). The pre-deploy gate has always rejected any exit code outside
    {0, 1}; the on-demand path did not, so a crashed scanner was stored as a
    completed scan with zero findings and the Security page called the
    project clean.
    """
    runner = "run_gitleaks" if tool == "gitleaks" else "run_pip_audit"
    monkeypatch.setattr(
        tasks, runner,
        lambda *a, **k: RawResult(tool=tool, target="x", exit_code=2, stdout="boom"),
    )
    scan = Scan(project_id=project.id, tool=tool, target="https://github.com/org/x.git", status="queued")
    session.add(scan)
    session.commit()
    job = _new_job(session, project, "scan")

    stub_runners.scan_task(str(job.id), str(scan.id), str(project.id), tool, scan.target)

    session.refresh(job)
    session.refresh(scan)
    assert job.status == "failed"
    assert scan.status == "failed"
    assert scan.summary in (None, {})


def test_scan_task_rejects_evil_target(session, project, user, stub_runners, monkeypatch):
    def _no_clone(*a, **k):
        raise ValueError("Repository URL scheme must be https")

    monkeypatch.setattr(tasks, "_clone_repo", _no_clone)
    scan = Scan(project_id=project.id, tool="gitleaks", target="git@github.com:org/repo.git", status="queued")
    session.add(scan)
    session.commit()
    job = _new_job(session, project, "scan")

    stub_runners.scan_task(str(job.id), str(scan.id), str(project.id), "gitleaks", "git@github.com:org/repo.git")

    session.refresh(job)
    session.refresh(scan)
    assert job.status == "failed"
    assert scan.status == "failed"


def test_deploy_task_blocks_on_critical(session, project, user, stub_runners, monkeypatch):
    deployment = Deployment(
        project_id=project.id,
        service_name="users-service",
        repo_url="https://github.com/org/users.git",
        branch="main",
        port=8000,
        replicas=2,
        status="queued",
    )
    session.add(deployment)
    session.commit()
    job = Job(project_id=project.id, deployment_id=deployment.id, type="deploy", status="queued")
    session.add(job)
    session.commit()

    def _blocking_trivy(*a, **k):
        return RawResult(tool="trivy", target="img", stdout=TRIVY_BLOCKED)

    monkeypatch.setattr(tasks, "run_trivy", _blocking_trivy)

    stub_runners.deploy_task(str(job.id), str(deployment.id), str(project.id), str(user.id))

    session.refresh(job)
    session.refresh(deployment)
    assert job.status == "failed"
    assert deployment.status == "blocked"
    assert "blocked" in job.error_message.lower() or "critical" in job.error_message.lower()


def test_deploy_task_gates_pass_when_clean(session, project, user, stub_runners, monkeypatch):
    deployment = Deployment(
        project_id=project.id,
        service_name="users-service",
        repo_url="https://github.com/org/users.git",
        branch="main",
        port=8000,
        replicas=2,
        status="queued",
    )
    session.add(deployment)
    session.commit()
    job = Job(project_id=project.id, deployment_id=deployment.id, type="deploy", status="queued")
    session.add(job)
    session.commit()

    def _fake_kubectl(args, *a, **k):
        return _StubResult(exit_code=0, output="ok")

    monkeypatch.setattr(tasks, "kubectl", _fake_kubectl)
    monkeypatch.setattr(tasks, "kubectl_apply", lambda *a, **k: None)

    stub_runners.deploy_task(str(job.id), str(deployment.id), str(project.id), str(user.id))

    session.refresh(job)
    session.refresh(deployment)
    assert job.status == "succeeded"
    assert deployment.status == "live"
    assert deployment.live_url.endswith(".devops.local")


def test_deploy_task_persists_its_gate_scans(session, project, user, stub_runners, monkeypatch):
    """The console's Security page reads Scan rows via GET /projects/{id}/scans.

    Before this fix, deploy_task ran gitleaks/pip-audit/trivy as pure gates —
    it logged their summaries to the job log and then discarded the results,
    so the exact scans that block every deploy never appeared there. A tenant
    had to separately click "Run scan" and pay for a second, redundant scan
    just to see what the deploy pipeline had already found.
    """
    deployment = Deployment(
        project_id=project.id,
        service_name="users-service",
        repo_url="https://github.com/org/users.git",
        branch="main",
        port=8000,
        replicas=2,
        status="queued",
    )
    session.add(deployment)
    session.commit()
    job = Job(project_id=project.id, deployment_id=deployment.id, type="deploy", status="queued")
    session.add(job)
    session.commit()

    monkeypatch.setattr(tasks, "kubectl", lambda args, *a, **k: _StubResult(exit_code=0, output="ok"))
    monkeypatch.setattr(tasks, "kubectl_apply", lambda *a, **k: None)

    def _fake_clone_with_requirements(*a, **k):
        import pathlib
        import tempfile

        repo = pathlib.Path(tempfile.mkdtemp(prefix="fake-repo-")) / "repo"
        repo.mkdir(parents=True, exist_ok=True)
        (repo / "Dockerfile").write_text("FROM scratch\n")
        (repo / "requirements.txt").write_text("requests==2.31.0\n")
        return repo

    monkeypatch.setattr(tasks, "_clone_repo", _fake_clone_with_requirements)

    stub_runners.deploy_task(str(job.id), str(deployment.id), str(project.id), str(user.id))

    session.refresh(deployment)
    assert deployment.status == "live"

    scans = session.scalars(
        sa.select(Scan).where(Scan.project_id == project.id).order_by(Scan.tool)
    ).all()
    tools = {s.tool for s in scans}
    assert tools == {"gitleaks", "pip_audit", "trivy"}
    for scan in scans:
        assert scan.status == "completed"
        assert scan.deployment_id == deployment.id
        assert scan.summary is not None


def test_kubectl_apply_scopes_every_object_to_the_tenant_namespace(project, monkeypatch, tmp_path):
    """A manifest with no metadata.namespace of its own (like
    k8s/tekton/pipeline.yaml — a static, unrendered file) must not fall back
    to kubectl's default namespace.

    Before this fix, `kubectl apply -f` ran with no `-n` at all, so every
    tenant's "private" Tekton Pipeline/Task install landed in the same
    shared "default" namespace instead of its own — silently overwriting the
    previous tenant's copy on every provision, the opposite of the isolation
    `_install_tenant_pipeline`'s own docstring promises. Caught by inspecting
    a real cluster after two provisions: both tenants' objects were sitting
    in `default`, dated to each provision, while their own namespaces had
    none.
    """
    from controlplane.runners.sandbox import SandboxResult

    commands = []

    def _fake_run_sandbox(run):
        commands.append(run.command)
        return SandboxResult(exit_code=0, output="", duration_seconds=0.01)

    monkeypatch.setattr(tasks, "run_sandbox", _fake_run_sandbox)
    manifest = tmp_path / "pipeline.yaml"
    manifest.write_text("kind: Task\nmetadata:\n  name: clone\n")

    tasks.kubectl_apply([manifest], project, force_namespace=True)

    apply_cmds = [c for c in commands if "apply" in c]
    assert apply_cmds, "kubectl apply was never invoked"
    expected_ns = tasks.k8s_namespace(project.id)
    for cmd in apply_cmds:
        assert "-n" in cmd, f"apply ran with no namespace flag: {cmd}"
        assert cmd[cmd.index("-n") + 1] == expected_ns, f"apply scoped to the wrong namespace: {cmd}"


def test_kubectl_apply_does_not_force_namespace_by_default(project, monkeypatch, tmp_path):
    """render_namespace()'s own output must keep working: it deliberately
    includes a ServiceMonitor addressed to the `monitoring` namespace, not
    the tenant's — forcing `-n <tenant ns>` on every object in that manifest
    set makes kubectl reject that one. Reproduced live: the first version of
    the force-namespace fix broke every new provision with "the namespace
    from the provided object 'monitoring' does not match the namespace
    'p-...'" the moment it ran against a real cluster."""
    from controlplane.runners.sandbox import SandboxResult

    commands = []

    def _fake_run_sandbox(run):
        commands.append(run.command)
        return SandboxResult(exit_code=0, output="", duration_seconds=0.01)

    monkeypatch.setattr(tasks, "run_sandbox", _fake_run_sandbox)
    manifest = tmp_path / "namespace.yaml"
    manifest.write_text("kind: Namespace\nmetadata:\n  name: p-x\n")

    tasks.kubectl_apply([manifest], project)

    apply_cmds = [c for c in commands if "apply" in c]
    assert apply_cmds, "kubectl apply was never invoked"
    for cmd in apply_cmds:
        assert "-n" not in cmd, f"apply forced a namespace when it must not have: {cmd}"


def test_persist_tekton_scan_results_reads_the_report_out_of_pod_logs(session, project):
    """Before this, deploying through Tekton left the Scan table completely
    empty: gitleaks/pip-audit/trivy run as Pods in the tenant's own
    namespace and nothing ever read their output back — reproduced live,
    six real Tekton deploys, zero rows in `scans`. Each scan Task echoes its
    JSON report between markers in its own log specifically so this can
    find it, since there is no other channel back once the Pod is gone."""
    from controlplane.models import Deployment

    deployment = Deployment(
        project_id=project.id, service_name="svc", repo_url="https://github.com/org/repo.git",
        branch="main", port=8000, replicas=1, status="building",
    )
    session.add(deployment)
    session.commit()

    gitleaks_report = "===SCAN_REPORT_BEGIN===\n[]\n===SCAN_REPORT_END==="
    pip_audit_report = '===SCAN_REPORT_BEGIN===\n{"dependencies": []}\n===SCAN_REPORT_END==='
    trivy_report = '===SCAN_REPORT_BEGIN===\n{"Results": []}\n===SCAN_REPORT_END==='
    logs_by_pod = {
        "secret-scan-pod": gitleaks_report,
        "dependency-scan-pod": pip_audit_report,
        "scan-pod": trivy_report,
    }

    def fake_call(args):
        if args[0] == "get" and args[1] == "taskrun":
            return json.dumps({"items": [
                {"metadata": {"labels": {"tekton.dev/pipelineTask": "secret-scan"}},
                 "status": {"podName": "secret-scan-pod"}},
                {"metadata": {"labels": {"tekton.dev/pipelineTask": "dependency-scan"}},
                 "status": {"podName": "dependency-scan-pod"}},
                {"metadata": {"labels": {"tekton.dev/pipelineTask": "scan"}},
                 "status": {"podName": "scan-pod"}},
                {"metadata": {"labels": {"tekton.dev/pipelineTask": "build"}},
                 "status": {"podName": "build-pod"}},
            ]})
        if args[0] == "logs":
            return logs_by_pod[args[1]]
        return ""

    caller = type("C", (), {"call": staticmethod(fake_call)})()
    tasks._persist_tekton_scan_results(session, caller, "p-x", "deploy-abc", deployment, "img:tag")

    scans = session.scalars(
        sa.select(Scan).where(Scan.project_id == project.id).order_by(Scan.tool)
    ).all()
    assert {s.tool for s in scans} == {"gitleaks", "pip_audit", "trivy"}
    for scan in scans:
        assert scan.status == "completed"
        assert scan.deployment_id == deployment.id


def test_delete_tekton_run_pods_uses_the_pipelinerun_label():
    """Every stage Pod (clone/build/scan/etc) still mounts the run's "source"
    workspace PVC, so deleting the PVC before these are gone never actually
    finishes: pvc-protection refuses to release it while any Pod, even a
    finished one, still references it. Reproduced live: every deploy's PVC
    sat stuck "Terminating" for hours, still counted against the tenant's
    fixed persistentvolumeclaims quota, while the leftover Pods themselves
    piled up as noise on the console's own Workloads page."""
    calls = []
    caller = type("C", (), {"call": staticmethod(lambda args: calls.append(args))})()

    tasks._delete_tekton_run_pods(caller, "p-x", "deploy-abc")

    assert calls == [["delete", "pods", "--namespace=p-x", "-l", "tekton.dev/pipelineRun=deploy-abc", "--ignore-not-found"]]


def test_delete_tekton_run_pods_is_best_effort():
    caller = type("C", (), {"call": staticmethod(lambda args: (_ for _ in ()).throw(RuntimeError("kubectl down")))})()
    tasks._delete_tekton_run_pods(caller, "p-x", "deploy-abc")  # must not raise


def test_delete_tekton_workspace_pvc_finds_the_pvc_by_owner_reference():
    """The PVC a PipelineRun's "source" workspace creates carries no label
    tying it to the run — only an ownerReference — so it has to be found by
    scanning every PVC in the namespace. Left uncleaned, one 2Gi PVC
    accumulates per deploy attempt (success or fail) against the tenant's
    fixed persistentvolumeclaims quota: reproduced live deploying six
    services with a couple of retries each — the ninth PipelineRun's PVC
    creation failed with "exceeded quota ... used: 8, limited: 8" and every
    deploy after that failed the same way, with no self-service recovery."""
    calls = []

    def fake_call(args):
        calls.append(args)
        if args[:2] == ["get", "pvc"]:
            return json.dumps({"items": [
                {"metadata": {"name": "pvc-unrelated", "ownerReferences": [
                    {"kind": "PipelineRun", "name": "some-other-run"},
                ]}},
                {"metadata": {"name": "pvc-target", "ownerReferences": [
                    {"kind": "PipelineRun", "name": "deploy-abc"},
                ]}},
            ]})
        return ""

    caller = type("C", (), {"call": staticmethod(fake_call)})()
    tasks._delete_tekton_workspace_pvc(caller, "p-x", "deploy-abc")

    delete_calls = [c for c in calls if c[:2] == ["delete", "pvc"]]
    assert delete_calls == [["delete", "pvc", "pvc-target", "--namespace=p-x", "--ignore-not-found"]]


def test_delete_tekton_workspace_pvc_is_best_effort(monkeypatch):
    """Must never raise and turn a successful deploy into a failed job over
    freeing scratch space."""
    caller = type("C", (), {"call": staticmethod(lambda args: (_ for _ in ()).throw(RuntimeError("kubectl down")))})()
    tasks._delete_tekton_workspace_pvc(caller, "p-x", "deploy-abc")  # must not raise


def test_tekton_kubectl_apply_manifest_mounts_the_file_into_the_sandbox(project, monkeypatch):
    """`kubectl(args, project)` only mounts the kubeconfig — `apply -f <path>`
    additionally needs the manifest itself visible inside the sandbox
    container. Before this fix, KubectlCaller's default `apply()` wrote the
    PipelineRun to a host tempfile and handed the sandboxed kubectl that
    host path directly, which failed with "the path ... does not exist" on
    every real Tekton deploy — _tekton_kubectl's apply_manifest must instead
    mount the containing directory in and reference the mounted path."""
    from controlplane.runners.sandbox import SandboxResult

    runs = []

    def _fake_run_sandbox(run):
        runs.append(run)
        return SandboxResult(exit_code=0, output="", duration_seconds=0.01)

    monkeypatch.setattr(tasks, "run_sandbox", _fake_run_sandbox)

    caller = tasks._tekton_kubectl(project)
    caller.apply_manifest({"metadata": {"name": "deploy-1"}})

    assert len(runs) == 1
    run = runs[0]
    assert "-f" in run.command
    manifest_arg = run.command[run.command.index("-f") + 1]
    # The referenced path must be one of the mounted container paths, not a
    # bare host filesystem path the sandbox has never seen.
    mounted_container_paths = [dst for _src, dst, _ro in run.mounts]
    assert any(manifest_arg.startswith(p) for p in mounted_container_paths), (
        f"apply -f references {manifest_arg!r}, which is not under any mounted path {mounted_container_paths}"
    )


def test_install_registry_credentials_secret_creates_a_usable_dockerconfig(project, monkeypatch):
    """render_pipelinerun()'s docker-credentials workspace names a Secret
    called `registry-credentials` in the tenant's own namespace, but nothing
    ever created it — kaniko's build Pod sat in Init forever with
    "MountVolume.SetUp failed ... secret 'registry-credentials' not found",
    reproduced on a real Tekton deploy against this platform's own local
    registry (which needs no auth at all)."""
    import base64
    import json
    import uuid as _uuid

    calls = []
    monkeypatch.setattr(tasks, "kubectl_apply", lambda paths, *a, **k: calls.append(paths[0].read_text()))

    tasks._install_registry_credentials_secret(str(_uuid.uuid4()), project)

    assert len(calls) == 1
    secret = json.loads(calls[0])
    assert secret["kind"] == "Secret"
    assert secret["metadata"]["name"] == "registry-credentials"
    assert secret["type"] == "kubernetes.io/dockerconfigjson"
    dockerconfig = json.loads(base64.b64decode(secret["data"][".dockerconfigjson"]))
    assert "auths" in dockerconfig


def test_install_tenant_dashboard_applies_the_manifest_force_namespaced(project, monkeypatch):
    """_install_tenant_dashboard must reuse the same force_namespace path as
    _install_tenant_pipeline: k8s/tekton/dashboard.yaml carries no
    metadata.namespace (one static file serves every tenant), so applying it
    without `-n <tenant ns>` would land every tenant's "private" Dashboard in
    the same shared namespace, exactly the bug
    test_kubectl_apply_scopes_every_object_to_the_tenant_namespace caught for
    the Pipeline install."""
    import uuid as _uuid

    calls = []
    monkeypatch.setattr(
        tasks,
        "kubectl_apply",
        lambda paths, project, on_line=None, force_namespace=False: calls.append(
            (paths[0].name, force_namespace)
        ),
    )

    tasks._install_tenant_dashboard(str(_uuid.uuid4()), project)

    assert calls == [("dashboard.yaml", True)]


def test_undeploy_task_deletes_manifests(session, project, user, stub_runners, monkeypatch):
    calls = []

    def _fake_kubectl(args, *a, **k):
        calls.append(args)
        return _StubResult(exit_code=0, output="ok")

    monkeypatch.setattr(tasks, "kubectl", _fake_kubectl)
    job = _new_job(session, project, "deploy")
    stub_runners.undeploy_task(str(job.id), "users-service", str(project.id), str(user.id))

    session.refresh(job)
    assert job.status == "succeeded"
    # kubectl is called as ["delete", <kind>, <name>, "--namespace=<ns>", ...]
    kinds = [call[1] for call in calls]
    assert kinds == ["deployment", "service", "ingress"]
    ns = tasks.k8s_namespace(project.id)
    assert all(f"--namespace={ns}" in call for call in calls)


def test_cancelled_job_is_noop(session, project, user, stub_runners):
    job = _new_job(session, project, "provision")
    job.cancel_requested = True
    session.commit()
    stub_runners.provision_task(str(job.id), str(project.id), str(user.id), SPEC, "/tmp/ws")
    session.refresh(job)
    assert job.status == "queued"  # untouched


def test_find_requirements_prefers_repo_root():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        (repo / "requirements.txt").write_text("flask\n")
        (repo / "app").mkdir()
        (repo / "app" / "requirements.txt").write_text("django\n")
        assert tasks._find_requirements(repo).name == "requirements.txt"


def test_find_requirements_raises_when_missing():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        with pytest.raises(FileNotFoundError):
            tasks._find_requirements(Path(tmp))


def test_safe_json_handles_malformed():
    assert tasks._safe_json("not json") == {"raw": "not json"}
    assert tasks._safe_json('{"a": 1}') == {"a": 1}


def test_append_log_truncates_keeping_tail(session, project, user):
    from controlplane.workers.tasks import _LOG_CAP, _append_log

    job = Job(project_id=project.id, type="provision", status="queued")
    session.add(job)
    session.commit()

    head = "x" * 150_000
    tail = "y" * 150_000
    _append_log(job.id, head)
    _append_log(job.id, tail)

    session.expire_all()
    job = session.get(Job, job.id)
    assert len(job.log) <= _LOG_CAP
    assert "truncated" in job.log
    assert job.log.rstrip().endswith("y" * 5000)
    assert not job.log.lstrip().startswith("x")
    assert job.log.count("truncated") == 1


def test_destroy_task_never_touches_a_differently_owned_same_named_namespace(
    session, user, team, monkeypatch
):
    """The bug Phase 2 (multi-tenancy plan) closes: two teams each naming a
    project "staging" must not share a namespace, and destroying one must
    never issue a `kubectl delete namespace` that could reach the other's —
    project names are unique only per team (models/project.py), not
    globally."""
    from controlplane.core.security import hash_password
    from controlplane.core.validation import k8s_namespace
    from controlplane.models import User
    from controlplane.repositories.teams import ensure_personal_team

    ns_spec = {"version": 1, "project": "staging", "mode": "namespace", "network": {}, "nodes": []}

    project_a = Project(owner_id=user.id, team_id=team.id, name="staging", status="ready", infra_spec=ns_spec)
    session.add(project_a)
    session.flush()

    other_user = User(email="other-team@example.com", password_hash=hash_password("Sup3rSecret!"))
    session.add(other_user)
    session.flush()
    other_team = ensure_personal_team(session, other_user)
    project_b = Project(owner_id=other_user.id, team_id=other_team.id, name="staging", status="ready", infra_spec=ns_spec)
    session.add(project_b)
    session.commit()

    ns_a = k8s_namespace(project_a.id)
    ns_b = k8s_namespace(project_b.id)
    assert ns_a != ns_b

    deleted = []

    def _fake_kubectl(args, *a, **k):
        if args[:2] == ["delete", "namespace"]:
            deleted.append(args[2])
        return _StubResult(exit_code=0, output="ok")

    monkeypatch.setattr(tasks, "kubectl", _fake_kubectl)

    job = Job(project_id=project_a.id, type="destroy", status="queued")
    session.add(job)
    session.commit()

    tasks.destroy_task(str(job.id), str(project_a.id), "", "staging", str(user.id))

    assert deleted == [ns_a]
    assert ns_b not in deleted


def test_destroy_task_completes_when_project_row_already_deleted(
    session, user, team, monkeypatch
):
    """DELETE /projects/{id} queues the destroy job *and* deletes the row, so
    the worker races the API. Found end-to-end: the teardown succeeded but
    the final status update matched 0 rows, poisoning the session and leaving
    the job stuck in "running" forever."""
    ns_spec = {"version": 1, "project": "doomed", "mode": "namespace", "network": {}, "nodes": []}
    project = Project(owner_id=user.id, team_id=team.id, name="doomed", status="destroying", infra_spec=ns_spec)
    session.add(project)
    session.commit()
    project_id = project.id

    job = Job(project_id=project_id, type="destroy", status="queued")
    session.add(job)
    session.commit()
    job_id = job.id

    def _fake_kubectl(args, *a, **k):
        # Simulate the API deleting the row while the teardown is in flight.
        from controlplane.db import SessionLocal

        with SessionLocal() as other:
            other.query(Job).filter(Job.id == job_id).update({"project_id": None})
            other.query(Project).filter(Project.id == project_id).delete()
            other.commit()
        return _StubResult(exit_code=0, output="ok")

    monkeypatch.setattr(tasks, "kubectl", _fake_kubectl)

    # Must not raise, and must mark the job finished rather than hanging.
    tasks.destroy_task(str(job_id), str(project_id), "", "doomed", str(user.id))

    session.expire_all()
    assert session.get(Job, job_id).status == "succeeded"


def test_reap_stale_jobs_unlocks_a_project_whose_worker_died(session, user, team):
    """A SIGKILLed worker never runs its except block, so the Job row stays
    "running" forever. get_active_provision_job treats that as an in-flight
    lock, so provision and destroy both 409 permanently and the expiry reaper
    skips the project too — it can never be rebuilt, removed or reaped."""
    from datetime import UTC, datetime, timedelta

    from controlplane.core.config import settings
    from controlplane.repositories.base import Scope
    from controlplane.repositories.projects import ProjectRepository

    project = Project(
        owner_id=user.id, team_id=team.id, name="wedged", status="provisioning",
        infra_spec={"version": 1, "project": "wedged", "mode": "namespace", "network": {}, "nodes": []},
    )
    session.add(project)
    session.flush()

    dead = Job(project_id=project.id, type="provision", status="running")
    # Older than Celery's own hard time limit, so it cannot still be alive.
    dead.started_at = datetime.now(UTC) - timedelta(
        seconds=settings.provision_timeout_seconds + 120 + 600
    )
    session.add(dead)
    session.commit()

    scope = Scope.from_session(session, user.id)
    repo = ProjectRepository(session, scope)
    # The lock the API checks before allowing provision or destroy.
    assert repo.get_active_provision_job(project.id) is not None

    assert tasks.reap_stale_jobs()["failed"] == 1

    session.expire_all()
    assert session.get(Job, dead.id).status == "failed"
    assert session.get(Project, project.id).status == "failed"
    # Project is usable again: no in-flight job is blocking provision/destroy.
    assert repo.get_active_provision_job(project.id) is None


def test_reap_stale_jobs_leaves_a_running_job_alone(session, user, team):
    """A job inside its time budget is still doing real work — failing it
    would abort a live provision."""
    from datetime import UTC, datetime, timedelta

    project = Project(
        owner_id=user.id, team_id=team.id, name="inflight", status="provisioning",
        infra_spec={"version": 1, "project": "inflight", "mode": "namespace", "network": {}, "nodes": []},
    )
    session.add(project)
    session.flush()

    fresh = Job(project_id=project.id, type="provision", status="running")
    fresh.started_at = datetime.now(UTC) - timedelta(seconds=30)
    session.add(fresh)
    session.commit()

    assert tasks.reap_stale_jobs()["failed"] == 0
    session.expire_all()
    assert session.get(Job, fresh.id).status == "running"
    assert session.get(Project, project.id).status == "provisioning"


def test_scan_task_fails_the_job_when_its_scan_row_is_missing(session, project, user):
    """The worker must never return silently on a missing Scan row: that left
    the job "running" forever with nothing to explain it. Guards the
    dispatch-before-commit race the scans router now avoids."""
    import uuid as _uuid

    job = _new_job(session, project, "scan")
    tasks.scan_task(str(job.id), str(_uuid.uuid4()), str(project.id), "trivy", "nginx:alpine")

    session.expire_all()
    refreshed = session.get(Job, job.id)
    assert refreshed.status == "failed"
    assert "not found" in (refreshed.error_message or "")


# ---------------------------------------------------------------------------
# job_steps rows — the pipeline graph's source of truth
# ---------------------------------------------------------------------------


def test_step_records_rows_with_indexes_totals_and_timestamps(session, project, user):
    from controlplane.models import JobStep

    job = _new_job(session, project, "provision")
    tasks._step(job.id, 1, 4, "terraform init")
    tasks._step(job.id, 2, 4, "terraform apply")
    tasks._step(job.id, 3, 4, "capturing node IPs")
    tasks._step(job.id, 4, 4, "ansible-playbook configure")
    tasks._mark_job(session, job.id, "succeeded")

    session.expire_all()
    steps = session.scalars(
        sa.select(JobStep).where(JobStep.job_id == job.id).order_by(JobStep.step_index)
    ).all()
    assert [s.step_index for s in steps] == [1, 2, 3, 4]
    assert all(s.step_total == 4 for s in steps)
    assert [s.name for s in steps] == [
        "terraform init", "terraform apply", "capturing node IPs", "ansible-playbook configure",
    ]
    # Every step is closed by the next one (or by _mark_job) and timestamped.
    assert all(s.status == "succeeded" for s in steps)
    for s in steps:
        assert s.started_at is not None
        assert s.finished_at is not None
        assert s.finished_at >= s.started_at


def test_step_reopening_the_same_index_updates_rather_than_crashes(session, project, user):
    """A Tekton deploy calls _step at the same index more than once:
    deploy_task opens step 1 itself for its own pre-clone (reading
    .platform.yml before the pipeline shape is known), and Tekton's own
    "clone" TaskRun reports through that same index again via
    _tekton_build_and_scan's report() callback. (job_id, step_index) is
    unique, so the second call used to raise a UniqueViolation and crash the
    whole deploy — reproduced on a real Tekton deploy, where the pipeline
    itself had already succeeded by the time the job was reported "failed"."""
    from controlplane.models import JobStep

    job = _new_job(session, project, "deploy")
    tasks._step(job.id, 1, 9, "cloning repository")
    tasks._step(job.id, 1, 9, "cloning repository")  # must not raise

    session.expire_all()
    steps = session.scalars(
        sa.select(JobStep).where(JobStep.job_id == job.id, JobStep.step_index == 1)
    ).all()
    assert len(steps) == 1
    assert steps[0].status == "running"


def test_step_emits_n_of_n_markers_to_the_log(session, project, user):
    job = _new_job(session, project, "provision")
    tasks._step(job.id, 1, 3, "one")
    tasks._step(job.id, 2, 3, "two")
    tasks._step(job.id, 3, 3, "three")

    session.expire_all()
    log = session.get(Job, job.id).log
    assert "[1/3]" in log and "[2/3]" in log and "[3/3]" in log


def test_failed_job_carries_error_into_the_final_step_detail(session, project, user):
    from controlplane.models import JobStep

    job = _new_job(session, project, "deploy")
    tasks._step(job.id, 1, 7, "cloning repository")
    tasks._step(job.id, 2, 7, "building image")
    tasks._mark_job(session, job.id, "failed", error="docker build failed: OOM")

    session.expire_all()
    job = session.get(Job, job.id)
    assert job.status == "failed"
    assert job.error_message == "docker build failed: OOM"
    steps = session.scalars(
        sa.select(JobStep).where(JobStep.job_id == job.id).order_by(JobStep.step_index)
    ).all()
    assert steps[0].status == "succeeded"
    assert steps[1].status == "failed"
    assert steps[1].error_message == "docker build failed: OOM"
    assert steps[1].finished_at is not None


def test_failed_job_truncates_error_detail_at_500_chars(session, project, user):
    from controlplane.models import JobStep

    job = _new_job(session, project, "deploy")
    tasks._step(job.id, 1, 1, "build image")
    tasks._mark_job(session, job.id, "failed", error="x" * 2000)

    session.expire_all()
    steps = session.scalars(
        sa.select(JobStep).where(JobStep.job_id == job.id).order_by(JobStep.step_index)
    ).all()
    assert len(steps) == 1
    assert len(steps[0].error_message) == 500


def test_log_truncation_keeps_all_seven_steps(session, project, user):
    """The log is capped head-first at 200 kB; the graph must not lose steps."""
    from controlplane.models import JobStep
    from controlplane.workers.tasks import _LOG_CAP, _append_log

    job = _new_job(session, project, "deploy")
    step_names = [
        "cloning repository", "building image", "pushing image to registry",
        "trivy scan + gate", "rendering + applying manifests",
        "waiting for rollout", "capturing live URL",
    ]
    for i, name in enumerate(step_names, start=1):
        tasks._step(job.id, i, 7, name)
        _append_log(job.id, f"step {i} output: " + "x" * 40_000)
    tasks._mark_job(session, job.id, "succeeded")

    session.expire_all()
    job = session.get(Job, job.id)
    assert len(job.log) <= _LOG_CAP
    steps = session.scalars(
        sa.select(JobStep).where(JobStep.job_id == job.id).order_by(JobStep.step_index)
    ).all()
    assert len(steps) == 7
    assert [s.step_index for s in steps] == [1, 2, 3, 4, 5, 6, 7]
    assert steps[6].status == "succeeded"
