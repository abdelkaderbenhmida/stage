"""Progressive delivery tests (docs/TODO.md Task 5.2).

Rollout/AnalysisTemplate rendering is exercised through the same
`_render_manifests` path the deploy task uses; no cluster is needed. The
deploy-task test asserts the kubectl verbs issued for a canary strategy.
"""

import json
import uuid

import pytest
import yaml
from controlplane.core.runtime import deployment_manifests_dir
from controlplane.core.validation import k8s_namespace
from controlplane.workers import tasks

TRIVY_OK = json.dumps({"Results": []})
SPEC = {"version": 1, "project": "tenant-a", "network": {}, "nodes": []}


class _StubResult:
    def __init__(self, exit_code=0, output="", timed_out=False):
        self.exit_code = exit_code
        self.output = output
        self.timed_out = timed_out


def _split_docs(path) -> list[dict]:
    return list(yaml.safe_load_all(path.read_text()))


def _render(tmp_path, strategy: str, mode: str = "vm", settings_override=None):
    """Render manifests for a canary/bluegreen/deployment service."""
    assert settings_override is not None, "pass the settings_override fixture"
    with settings_override(workspace_root=str(tmp_path)):
        pid = uuid.uuid4()
        project = type(
            "P",
            (),
            {
                "id": pid,
                "name": "tenant-a",
                "team_id": uuid.uuid4(),
                "infra_spec": {"version": 1, "project": "tenant-a", "network": {}, "nodes": [], "mode": mode},
            },
        )()
        deployment = type(
            "D",
            (),
            {
                "id": uuid.uuid4(),
                "service_name": "users-service",
                "repo_url": "https://github.com/org/users.git",
                "branch": "main",
                "port": 8000,
                "replicas": 2,
                "strategy": strategy,
                # A real Deployment always carries these; the stub has to as
                # well, or it stops describing the thing under test.
                "env_vars": {},
                "secret_keys": [],
                "health_path": "/livez",
            },
        )()
        manifests = tasks._render_manifests(project, deployment, "registry/img:commit-abc123")
        out_dir = deployment_manifests_dir(pid, mode)
    return manifests, out_dir, k8s_namespace(pid)


def test_canary_strategy_renders_rollout_with_slo_analysis(tmp_path, settings_override):
    manifests, ws, ns = _render(tmp_path, "canary", settings_override=settings_override)
    names = {p.name for p in manifests}
    assert names == {"rollout.yaml", "analysis.yaml", "service.yaml", "ingress.yaml"}
    assert "deployment.yaml" not in names

    rollout = _split_docs(ws / "rollout.yaml")[0]
    assert rollout["kind"] == "Rollout"
    assert rollout["apiVersion"] == "argoproj.io/v1alpha1"
    canary = rollout["spec"]["strategy"]["canary"]
    steps = canary["steps"]
    assert [step["setWeight"] for step in steps if "setWeight" in step] == [20, 100]
    assert [step["pause"]["duration"] for step in steps if "pause" in step] == ["30s"]
    analysis_step = next(step for step in canary["steps"] if "analysis" in step)
    assert analysis_step["analysis"]["templates"][0]["templateName"] == "users-service-slo"
    container = rollout["spec"]["template"]["spec"]["containers"][0]
    assert container["image"] == "registry/img:commit-abc123"
    assert container["readinessProbe"]["httpGet"]["path"] == "/livez"

    analysis = _split_docs(ws / "analysis.yaml")[0]
    assert analysis["kind"] == "AnalysisTemplate"
    assert analysis["metadata"]["name"] == "users-service-slo"
    metrics = analysis["spec"]["metrics"]
    assert {m["name"] for m in metrics} == {"error-rate", "p95-latency"}
    # The analysis must be wired to the platform's Prometheus SLOs, not a toy
    # query: error rate = 5xx / total within this tenant namespace.
    error_rate = next(m for m in metrics if m["name"] == "error-rate")
    provider = error_rate["provider"]["prometheus"]
    assert provider["address"].startswith("http://prometheus.")
    assert f'namespace="{ns}"' in provider["query"]
    assert 'code=~"5.."' in provider["query"]
    assert "clamp_min" in provider["query"]


def test_bluegreen_strategy_renders_bluegreen_rollout(tmp_path, settings_override):
    manifests, ws, ns = _render(tmp_path, "bluegreen", settings_override=settings_override)
    names = {p.name for p in manifests}
    assert "rollout.yaml" in names and "analysis.yaml" in names
    rollout = _split_docs(ws / "rollout.yaml")[0]
    bg = rollout["spec"]["strategy"]["blueGreen"]
    assert bg["activeService"] == "users-service"
    assert bg["autoPromotionEnabled"] is False


def test_default_strategy_renders_plain_deployment(tmp_path, settings_override):
    manifests, ws, ns = _render(tmp_path, "deployment", settings_override=settings_override)
    names = {p.name for p in manifests}
    assert names == {"deployment.yaml", "service.yaml", "ingress.yaml"}
    doc = _split_docs(ws / "deployment.yaml")[0]
    assert doc["kind"] == "Deployment"


def test_namespace_mode_renders_outside_project_workspace(tmp_path, settings_override):
    """Namespace-mode projects have no Terraform workspace; their manifests
    must land under the namespace root, not pretend one exists (docs/TODO.md
    §8 item 1)."""
    manifests, out, ns = _render(tmp_path, "canary", mode="namespace", settings_override=settings_override)
    assert {p.name for p in manifests} == {"rollout.yaml", "analysis.yaml", "service.yaml", "ingress.yaml"}
    assert out.parts[-3:] == ("namespaces", out.parts[-2], "manifests")
    assert all(p.exists() for p in manifests)


@pytest.mark.integration
def test_canary_deploy_awaits_rollout_resource(session, monkeypatch, tmp_path):
    """The deploy task must wait on `rollout/<name>` for a canary deployment
    and never issue `rollout status deployment/...` (Task 5.2)."""
    from controlplane.core.security import hash_password
    from controlplane.models import Deployment, Job, Project, User
    from controlplane.repositories.teams import ensure_personal_team

    user = User(email="rollout@example.com", password_hash=hash_password("pw"), role="admin")
    session.add(user)
    session.commit()
    team = ensure_personal_team(session, user)
    project = Project(owner_id=user.id, team_id=team.id, name="tenant-a", status="draft", infra_spec=SPEC)
    session.add(project)
    session.commit()
    deployment = Deployment(
        project_id=project.id,
        service_name="users-service",
        repo_url="https://github.com/org/users.git",
        branch="main",
        port=8000,
        replicas=1,
        strategy="canary",
        status="queued",
    )
    session.add(deployment)
    session.commit()
    job = Job(project_id=project.id, deployment_id=deployment.id, type="deploy", status="queued")
    session.add(job)
    session.commit()

    calls: list[list[str]] = []

    def _fake_kubectl(args, *a, **k):
        calls.append(args)
        return _StubResult(exit_code=0, output="ok")

    def _ok(*a, **k):
        return _StubResult(exit_code=0, output="ok")

    from controlplane.runners.scanners.base import RawResult

    monkeypatch.setattr(tasks, "run_sandbox", _ok)
    monkeypatch.setattr(tasks, "_clone_repo", lambda *a, **k: tmp_path)
    # deploy_task refuses to build from a repository with no Dockerfile.
    (tmp_path / "Dockerfile").write_text("FROM scratch\n")
    monkeypatch.setattr(
        tasks, "run_trivy", lambda *a, **k: RawResult(tool="trivy", target="img", stdout=TRIVY_OK)
    )
    # deploy_task also gates on gitleaks and pip-audit now (same tools the
    # platform's own CI runs), and neither is stubbed by run_sandbox above:
    # both call run_sandbox through their own module (runners/scanners/), not
    # through tasks.py's reference to it, so patching tasks.run_sandbox alone
    # does not reach them.
    monkeypatch.setattr(tasks, "run_gitleaks", lambda *a, **k: RawResult(tool="gitleaks", target="x"))
    monkeypatch.setattr(tasks, "run_pip_audit", lambda *a, **k: RawResult(tool="pip_audit", target="x"))
    monkeypatch.setattr(tasks, "kubectl", _fake_kubectl)
    monkeypatch.setattr(tasks, "kubectl_apply", lambda *a, **k: None)

    tasks.deploy_task(str(job.id), str(deployment.id), str(project.id), str(user.id))

    session.refresh(job)
    session.refresh(deployment)
    assert job.status == "succeeded"
    assert deployment.status == "live"
    wait_calls = [c for c in calls if len(c) >= 2 and c[0] == "rollout" and c[1] == "status"]
    assert any("rollout/users-service" in c for c in wait_calls), calls
    assert not any("deployment/users-service" in c for c in wait_calls), calls