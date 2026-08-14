"""Celery tasks: provisioning, destroy, deployment pipeline, and scanning.

Workers never execute user-supplied code directly — every external tool runs
through the sandbox (docs/PLATFORM_SPEC.md §7.2). Job logs are scrubbed
before they are written to the database (§7.4).
"""

import json
import socket
import uuid
from datetime import UTC
from pathlib import Path

from sqlalchemy.orm import Session

from controlplane.core.config import settings
from controlplane.core.logging import request_id_var
from controlplane.core.pool import claim_cluster
from controlplane.core.redaction import scrub_line
from controlplane.core.repo_url import validate_repo_url
from controlplane.core.runtime import (
    ansible_runtime,
    deployment_manifests_dir,
    namespace_workspace,
    project_workspace,
    terraform_runtime,
    user_ssh_private_key,
)
from controlplane.db import SessionLocal
from controlplane.models import Deployment, Job, PooledCluster, Project, Scan
from controlplane.renderers import render_ansible, render_namespace, render_terraform
from controlplane.repositories.base import Scope
from controlplane.repositories.deployments import DeploymentRepository
from controlplane.repositories.jobs import ScanRepository
from controlplane.repositories.projects import ProjectRepository
from controlplane.runners.ansible_runner import ansible_playbook
from controlplane.runners.sandbox import SandboxResult, SandboxRun, run_sandbox
from controlplane.runners.scanners import run_gitleaks, run_pip_audit, run_trivy
from controlplane.runners.terraform_runner import (
    terraform_apply,
    terraform_destroy,
    terraform_init,
    terraform_output,
)
from controlplane.workers.celery_app import celery_app

TerminalStatuses = ("succeeded", "failed", "cancelled")


# Job logs live in an unbounded `text` column, so a verbose Terraform run
# could grow to megabytes (§8 item 8). Keep the tail — the part a debugging
# human actually reads — and say so, so nobody mistakes a truncated run for
# a short one. Head is dropped, not tail: an error trace ends, it does not
# begin.
_LOG_CAP = 200_000
_TRUNCATION_MARKER = f"\n[... truncated — first bytes dropped, keeping last {_LOG_CAP} bytes ...]\n"


def _append_log(job_id: uuid.UUID, line: str) -> None:
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if job is None:
            return
        new_log = job.log + scrub_line(line) + "\n"
        if len(new_log) > _LOG_CAP:
            marker = _TRUNCATION_MARKER if not job.log.startswith(_TRUNCATION_MARKER) else ""
            new_log = marker + new_log[-(_LOG_CAP - len(marker)):]
        job.log = new_log
        db.commit()


def _mark_job(db: Session, job_id: uuid.UUID, status: str, error: str | None = None) -> None:
    job = db.get(Job, job_id)
    if job is None:
        return
    job.status = status
    job.error_message = error
    if status in TerminalStatuses:
        from datetime import datetime

        job.finished_at = datetime.now(UTC)
    db.commit()


def _stamp_request_id(job: "Job") -> None:
    """Carry the API request id (§7 correlation) if queueing happened inside
    an HTTP request — Celery-queued work (beat, reaper) leaves it empty."""
    job.request_id = request_id_var.get()


def _fail(db: Session, job_id: uuid.UUID, message: str) -> None:
    _append_log(job_id, f"FAILED: {message}")
    _mark_job(db, job_id, "failed", message)


def _log_lines(job_id: uuid.UUID):
    def on_line(line: str) -> None:
        _append_log(job_id, line)

    return on_line


def _check(result) -> None:
    if result.timed_out:
        raise RuntimeError(f"command timed out: {result.output[-500:]}")
    if result.exit_code != 0:
        raise RuntimeError(f"command exited {result.exit_code}: {result.output[-800:]}")


# ---------------------------------------------------------------------------
# Provisioning
# ---------------------------------------------------------------------------

def queue_provision(project: Project, user_id: uuid.UUID) -> Job:
    db = SessionLocal()
    try:
        job = Job(project_id=project.id, type="provision", status="queued")
        _stamp_request_id(job)
        db.add(job)
        db.flush()
        # Namespace-mode projects never hold Terraform state; give them their
        # own workspace root instead of a fake project_workspace.
        mode = (project.infra_spec or {}).get("mode", "namespace")
        workspace = namespace_workspace(project.id) if mode == "namespace" else project_workspace(project.id)
        project.workspace_path = str(workspace)
        db.commit()
        result = provision_task.apply_async(
            args=[str(job.id), str(project.id), str(user_id), project.infra_spec, str(workspace)]
        )
        job.celery_task_id = result.id
        db.commit()
        return job
    finally:
        db.close()


@celery_app.task(name="controlplane.workers.tasks.provision_task")
def provision_task(
    job_id: str,
    project_id: str,
    user_id: str,
    spec_dict: dict,
    workspace: str,
) -> None:
    ws = Path(workspace)
    db = SessionLocal()
    try:
        job = db.get(Job, uuid.UUID(job_id))
        if job is None or job.cancel_requested:
            return
        job.status = "running"
        request_id_var.set(job.request_id or "")
        from datetime import datetime

        job.started_at = datetime.now(UTC)
        db.commit()

        from controlplane.schemas.spec import InfraSpec

        spec = InfraSpec.model_validate(spec_dict)
        on_line = _log_lines(job_id)

        # Namespace mode carves a bounded slice out of a shared cluster and
        # skips Terraform and Ansible entirely — seconds instead of minutes.
        if spec.mode == "namespace":
            _provision_namespace(db, job_id, project_id, spec, ws, on_line)
            return

        # A pre-warmed cluster of the same shape can be handed over instantly.
        pooled = claim_cluster(db, spec, uuid.UUID(project_id))
        if pooled is not None:
            db.commit()
            _adopt_pooled_cluster(db, job_id, project_id, user_id, pooled)
            return

        render_terraform(spec, terraform_runtime(uuid.UUID(user_id)), ws)
        render_ansible(spec, ansible_runtime(), ws)
        _append_log(job_id, "Rendered Terraform and Ansible artifacts.")

        _append_log(job_id, "[1/4] terraform init")
        _check(terraform_init(ws, on_line=on_line))

        _append_log(job_id, "[2/4] terraform apply")
        try:
            _check(terraform_apply(ws, on_line=on_line))
        except Exception:
            _append_log(job_id, "terraform apply failed — attempting cleanup of partially created resources")
            cleanup = terraform_destroy(ws, on_line=on_line)
            _append_log(job_id, f"cleanup result: {cleanup.exit_code}")
            raise

        _append_log(job_id, "[3/4] capturing node IPs")
        out = terraform_output(ws, "node_ips")
        _check(out)
        node_ips = json.loads(out.output)
        # The task runs as the user who queued it: their scope decides which
        # projects the writes may touch, and the job was authorized at queue
        # time by the router's role check.
        scope = Scope.from_session(db, uuid.UUID(user_id))
        for node_name, ip in node_ips.items():
            ProjectRepository(db, scope).update_node_ip(uuid.UUID(project_id), node_name, ip)
        db.commit()

        _append_log(job_id, "[4/4] ansible-playbook configure")
        key = user_ssh_private_key(uuid.UUID(user_id))
        _check(ansible_playbook(ws, key, on_line=on_line))

        _mark_ready(db, project_id)
        _mark_job(db, uuid.UUID(job_id), "succeeded")
        _append_log(job_id, "Provisioning complete.")
    except Exception as exc:  # noqa: BLE001
        project = db.get(Project, uuid.UUID(project_id))
        if project and project.status != "destroying":
            project.status = "failed"
            db.commit()
        _fail(db, uuid.UUID(job_id), str(exc))
    finally:
        db.close()


def _mark_ready(db: Session, project_id: str) -> None:
    """Flip a project to ready and start its TTL clock.

    The expiry countdown begins here rather than at creation, so time spent
    waiting for provisioning is not deducted from the user's environment.
    """
    from datetime import datetime, timedelta

    project = db.get(Project, uuid.UUID(project_id))
    if project is None:
        return
    project.status = "ready"
    project.last_accessed_at = datetime.now(UTC)
    project.expiry_warned = False
    if project.auto_destroy and project.ttl_hours:
        project.expires_at = datetime.now(UTC) + timedelta(hours=project.ttl_hours)
    db.commit()


def _provision_namespace(
    db: Session, job_id: str, project_id: str, spec, ws: Path, on_line
) -> None:
    """Provision by carving a quota-bounded namespace out of a shared cluster."""
    _append_log(job_id, "[1/2] rendering namespace, quota, limits and network policy")
    manifest = render_namespace(spec, ws)

    _append_log(job_id, "[2/2] applying to the shared cluster")
    project = db.get(Project, uuid.UUID(project_id))
    kubectl_apply([manifest], project, on_line)

    # There are no VMs in this mode; the recorded nodes describe the quota
    # rather than machines, so they are marked ready without an IP.
    from controlplane.models import Node

    for node in db.query(Node).filter(Node.project_id == uuid.UUID(project_id)).all():
        node.status = "running"
    db.commit()

    _mark_ready(db, project_id)
    _mark_job(db, uuid.UUID(job_id), "succeeded")
    _append_log(job_id, f"Namespace {spec.project} ready.")


def _adopt_pooled_cluster(
    db: Session, job_id: str, project_id: str, user_id: str, pooled: PooledCluster
) -> None:
    """Hand a pre-warmed cluster to the project instead of provisioning."""
    _append_log(job_id, "Claimed a pre-warmed cluster from the pool.")

    project = db.get(Project, uuid.UUID(project_id))
    if project is not None:
        project.workspace_path = pooled.workspace_path
        db.commit()

    if pooled.node_ips:
        scope = Scope.from_session(db, uuid.UUID(user_id))
        for node_name, ip in json.loads(pooled.node_ips).items():
            ProjectRepository(db, scope).update_node_ip(
                uuid.UUID(project_id), node_name, ip
            )
        db.commit()

    _mark_ready(db, project_id)
    _mark_job(db, uuid.UUID(job_id), "succeeded")
    _append_log(job_id, "Provisioning complete (warm pool).")


# ---------------------------------------------------------------------------
# Destroy
# ---------------------------------------------------------------------------

def queue_destroy(project_id: uuid.UUID, workspace: str | None, project_name: str, user_id: uuid.UUID) -> Job:
    db = SessionLocal()
    try:
        job = Job(project_id=project_id, type="destroy", status="queued")
        _stamp_request_id(job)
        db.add(job)
        db.flush()
        db.commit()
        result = destroy_task.apply_async(
            args=[str(job.id), str(project_id), workspace or "", project_name, str(user_id)]
        )
        job.celery_task_id = result.id
        db.commit()
        return job
    finally:
        db.close()


@celery_app.task(name="controlplane.workers.tasks.destroy_task")
def destroy_task(
    job_id: str,
    project_id: str,
    workspace: str,
    project_name: str,
    user_id: str,
) -> None:
    db = SessionLocal()
    try:
        job = db.get(Job, uuid.UUID(job_id))
        if job is None or job.cancel_requested:
            return
        job.status = "running"
        request_id_var.set(job.request_id or "")
        from datetime import datetime

        job.started_at = datetime.now(UTC)
        db.commit()

        ws = Path(workspace)
        on_line = _log_lines(job_id)

        project = db.get(Project, uuid.UUID(project_id))
        mode = (project.infra_spec or {}).get("mode", "vm") if project else "vm"

        # Namespace mode has no Terraform state — deleting the namespace
        # cascades to everything inside it.
        if mode == "namespace":
            _append_log(job_id, f"deleting namespace {project_name}")
            result = kubectl(
                ["delete", "namespace", project_name, "--ignore-not-found", "--wait=true"],
                project,
                on_line=on_line,
            )
            if result.exit_code != 0:
                _set_project_status(db, project_id, "failed")
                _mark_job(db, uuid.UUID(job_id), "failed", result.output[-800:])
                return
            import shutil

            shutil.rmtree(ws, ignore_errors=True)
            _set_project_status(db, project_id, "destroyed")
            _mark_job(db, uuid.UUID(job_id), "succeeded")
            _append_log(job_id, f"Namespace {project_name} removed.")
            return

        if ws.exists():
            _append_log(job_id, "terraform destroy")
            result = terraform_destroy(ws, on_line=on_line)
            if result.exit_code != 0:
                _append_log(job_id, f"destroy exit {result.exit_code}")
                _set_project_status(db, project_id, "failed")
                _mark_job(db, uuid.UUID(job_id), "failed", result.output[-800:])
                return
            import shutil

            shutil.rmtree(ws, ignore_errors=True)
            _append_log(job_id, "Workspace removed.")
        else:
            _append_log(job_id, "No workspace found — nothing to destroy.")

        # Without this the project sits in `destroying` forever: the job
        # reports success but the project it acted on is never updated, and it
        # can no longer be edited or re-provisioned.
        _set_project_status(db, project_id, "destroyed")
        _mark_job(db, uuid.UUID(job_id), "succeeded")
        _append_log(job_id, f"Destroy of {project_name} complete.")
    except Exception as exc:  # noqa: BLE001
        _set_project_status(db, project_id, "failed")
        _fail(db, uuid.UUID(job_id), str(exc))
    finally:
        db.close()


def _set_project_status(db: Session, project_id: str, status: str) -> None:
    project = db.get(Project, uuid.UUID(project_id))
    if project is not None:
        project.status = status
        db.commit()


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------

def queue_scan(scan: Scan, project_id: uuid.UUID) -> Job:
    db = SessionLocal()
    try:
        job = Job(project_id=project_id, deployment_id=scan.deployment_id, type="scan", status="queued")
        _stamp_request_id(job)
        db.add(job)
        db.flush()
        db.commit()
        result = scan_task.apply_async(
            args=[str(job.id), str(scan.id), str(project_id), scan.tool, scan.target]
        )
        job.celery_task_id = result.id
        db.commit()
        # Return the Job like the sibling queue_* functions do — the earlier
        # inconsistency broke a caller that assumed a return value.
        db.refresh(job)
        return job
    finally:
        db.close()


@celery_app.task(name="controlplane.workers.tasks.scan_task")
def scan_task(job_id: str, scan_id: str, project_id: str, tool: str, target: str) -> None:
    db = SessionLocal()
    try:
        job = db.get(Job, uuid.UUID(job_id))
        if job is None or job.cancel_requested:
            return
        job.status = "running"
        request_id_var.set(job.request_id or "")
        from datetime import datetime

        job.started_at = datetime.now(UTC)
        db.commit()
        # The scan was authorized at queue time by the router's role check.
        # The task itself runs system-wide: it only ever touches the Scan row
        # it was handed, never a project of its own choosing.
        repo = ScanRepository(db, Scope.system())
        scan = db.get(Scan, uuid.UUID(scan_id))
        if scan is None:
            return
        repo.set_result(scan, "running")
        db.commit()

        on_line = _log_lines(job_id)
        try:
            if tool == "trivy":
                result = run_trivy(target, on_line=on_line)
                from controlplane.parsers.trivy_parser import parse_trivy

                parsed = parse_trivy(result.stdout)
            elif tool == "gitleaks":
                cloned = _clone_repo(target, job_id, on_line)
                result = run_gitleaks(cloned, on_line=on_line)
                raw = result.artifact_path
                text = Path(raw).read_text() if raw and Path(raw).exists() else "[]"
                from controlplane.parsers.gitleaks_parser import parse_gitleaks

                parsed = parse_gitleaks(text)
                import shutil

                shutil.rmtree(cloned.parent, ignore_errors=True)
            else:  # pip_audit
                cloned = _clone_repo(target, job_id, on_line)
                requirements = _find_requirements(cloned)
                result = run_pip_audit(requirements, on_line=on_line)
                from controlplane.parsers.pip_audit_parser import parse_pip_audit

                parsed = parse_pip_audit(result.stdout)
                import shutil

                shutil.rmtree(cloned.parent, ignore_errors=True)

            scan = db.get(Scan, uuid.UUID(scan_id))
            # The scan was authorized at queue time by the router's role check.
            # The task itself runs system-wide: it only ever touches the Scan row
            # it was handed, never a project of its own choosing.
            repo = ScanRepository(db, Scope.system())
            repo.set_result(
                scan, "completed",
                raw_output=_safe_json(result.stdout),
                summary=parsed.summary,
                duration_seconds=result.duration_seconds,
            )
            repo.add_findings(scan, parsed.findings)
            db.commit()
            _mark_job(db, uuid.UUID(job_id), "succeeded")
            _append_log(job_id, f"{tool} scan complete: {json.dumps(parsed.summary)}")
        except Exception as exc:  # noqa: BLE001
            scan = db.get(Scan, uuid.UUID(scan_id))
            if scan:
                ScanRepository(db, Scope.system()).set_result(scan, "failed")
            _fail(db, uuid.UUID(job_id), str(exc))
    finally:
        db.close()


def _safe_json(text: str) -> dict:
    try:
        data = json.loads(text)
        return data if isinstance(data, dict | list) else {"raw": text[:5000]}
    except json.JSONDecodeError:
        return {"raw": text[:5000]}


def _clone_repo(repo_url: str, job_id: str, on_line) -> Path:
    """Validate + clone into a fresh host-visible workspace. Returns the repo dir.

    The workspace root is mounted into the sandbox with the repo subdir
    writable, so the clone survives on the host for later scan/deploy steps.
    """
    validate_repo_url(repo_url)
    root = Path("/tmp/ctl-repos") / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=True)
    (root / "repo").mkdir(parents=True, exist_ok=True)
    target = root / "repo"
    _append_log(job_id, f"cloning {repo_url}")
    result = run_sandbox(
        SandboxRun(
            command=["git", "clone", "--depth", "1", repo_url, str(target)],
            workspace=root,
            writable_paths=["repo"],
            network_enabled=True,
            timeout_seconds=settings.scan_timeout_seconds,
            on_line=on_line,
        )
    )
    _check(result)
    return target


def _find_requirements(repo: Path) -> Path:
    candidates = [repo / "requirements.txt", repo / "app" / "requirements.txt", repo / "requirements" / "base.txt"]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError("No requirements.txt found in repository.")


# ---------------------------------------------------------------------------
# Deployment pipeline
# ---------------------------------------------------------------------------

def queue_deploy(deployment: Deployment, project: Project, user_id: uuid.UUID) -> Job:
    db = SessionLocal()
    try:
        job = Job(project_id=project.id, deployment_id=deployment.id, type="deploy", status="queued")
        _stamp_request_id(job)
        db.add(job)
        db.flush()
        db.commit()
        result = deploy_task.apply_async(
            args=[str(job.id), str(deployment.id), str(project.id), str(user_id)]
        )
        job.celery_task_id = result.id
        db.commit()
        return job
    finally:
        db.close()


def queue_undeploy(deployment: Deployment, project: Project, user_id: uuid.UUID) -> Job:
    db = SessionLocal()
    try:
        job = Job(project_id=project.id, deployment_id=deployment.id, type="deploy", status="queued")
        _stamp_request_id(job)
        db.add(job)
        db.flush()
        db.commit()
        result = undeploy_task.apply_async(
            args=[str(job.id), _deployment_name(deployment), project.name, str(user_id)]
        )
        job.celery_task_id = result.id
        db.commit()
        return job
    finally:
        db.close()


@celery_app.task(name="controlplane.workers.tasks.deploy_task")
def deploy_task(job_id: str, deployment_id: str, project_id: str, user_id: str) -> None:
    db = SessionLocal()
    try:
        job = db.get(Job, uuid.UUID(job_id))
        if job is None or job.cancel_requested:
            return
        job.status = "running"
        request_id_var.set(job.request_id or "")
        from datetime import datetime

        job.started_at = datetime.now(UTC)
        db.commit()

        deployment = db.get(Deployment, uuid.UUID(deployment_id))
        if deployment is None:
            _mark_job(db, uuid.UUID(job_id), "failed", "deployment record missing")
            return
        project = db.get(Project, uuid.UUID(project_id))
        if project is None:
            _mark_job(db, uuid.UUID(job_id), "failed", "project record missing")
            return
        repo = DeploymentRepository(db, Scope.from_session(db, uuid.UUID(user_id)))
        on_line = _log_lines(job_id)
        image_ref = f"{settings.registry}/{project.name}-{deployment.service_name}:commit-{uuid.uuid4().hex[:8]}"

        try:
            _append_log(job_id, "[1/7] cloning repository")
            cloned = _clone_repo(deployment.repo_url, job_id, on_line)
            repo.set_status(deployment, "building")
            db.commit()

            _append_log(job_id, "[2/7] building image")
            build = run_sandbox(
                SandboxRun(
                    command=["docker", "build", "-t", image_ref, "."],
                    workspace=cloned,
                    requires_docker_daemon=True,
                    timeout_seconds=settings.provision_timeout_seconds,
                    on_line=on_line,
                )
            )
            _check(build)
            import shutil

            shutil.rmtree(cloned.parent, ignore_errors=True)

            _append_log(job_id, "[3/7] pushing image to registry")
            push = run_sandbox(
                SandboxRun(
                    command=["docker", "push", image_ref],
                    requires_docker_daemon=True,
                    timeout_seconds=600,
                    on_line=on_line,
                    env={"REGISTRY_PASSWORD": settings.registry_password, "REGISTRY_USER": settings.registry_user},
                )
            )
            _check(push)

            _append_log(job_id, "[4/7] trivy scan + gate")
            repo.set_status(deployment, "scanning")
            db.commit()
            trivy = run_trivy(image_ref, on_line=on_line)
            from controlplane.parsers.trivy_parser import parse_trivy

            parsed = parse_trivy(trivy.stdout)
            gate = parsed.summary.get("critical", 0) + parsed.summary.get("high", 0)
            if gate > 0:
                repo.set_status(deployment, "blocked", image_ref=image_ref)
                db.commit()
                raise RuntimeError(
                    f"Image blocked: {parsed.summary['critical']} critical, "
                    f"{parsed.summary['high']} high. Gate on CRITICAL/HIGH."
                )

            _append_log(job_id, "[5/7] rendering + applying manifests")
            repo.set_status(deployment, "deploying", image_ref=image_ref)
            db.commit()
            manifests = _render_manifests(project, deployment, image_ref)
            kubectl_apply(manifests, project, on_line)

            _append_log(job_id, "[6/7] waiting for rollout")
            resource = "rollout" if deployment.strategy != "deployment" else "deployment"
            rollout = kubectl(
                ["rollout", "status", f"{resource}/{_deployment_name(deployment)}", f"--namespace={project.name}", "--timeout=180s"],
                project, on_line=on_line,
            )
            if rollout.exit_code != 0:
                _append_log(job_id, "rollout failed — rolling back")
                kubectl(["rollout", "undo", f"{resource}/{_deployment_name(deployment)}", f"--namespace={project.name}"], project, on_line=on_line)
                raise RuntimeError(f"rollout failed: {rollout.output[-500:]}")

            _append_log(job_id, "[7/7] capturing live URL")
            live_url = f"http://{_deployment_name(deployment)}.{project.name}.{_cluster_domain()}"
            repo.set_status(deployment, "live", image_ref=image_ref, live_url=live_url)
            db.commit()
            _mark_job(db, uuid.UUID(job_id), "succeeded")
            _append_log(job_id, f"Deployment live at {live_url}")
        except Exception as exc:  # noqa: BLE001
            if deployment.status not in ("blocked",):
                repo.set_status(deployment, "failed")
            db.commit()
            _fail(db, uuid.UUID(job_id), str(exc))
    finally:
        db.close()


@celery_app.task(name="controlplane.workers.tasks.undeploy_task")
def undeploy_task(job_id: str, deployment_name: str, namespace: str, user_id: str) -> None:
    db = SessionLocal()
    try:
        job = db.get(Job, uuid.UUID(job_id))
        if job is None or job.cancel_requested:
            return
        job.status = "running"
        request_id_var.set(job.request_id or "")
        from datetime import datetime

        job.started_at = datetime.now(UTC)
        db.commit()
        on_line = _log_lines(job_id)
        for kind in ("deployment", "service", "ingress"):
            kubectl(["delete", kind, deployment_name, f"--namespace={namespace}", "--ignore-not-found"], None, on_line=on_line)
        _mark_job(db, uuid.UUID(job_id), "succeeded")
    except Exception as exc:  # noqa: BLE001
        _fail(db, uuid.UUID(job_id), str(exc))
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Kubernetes helpers
# ---------------------------------------------------------------------------

def _deployment_name(deployment: Deployment) -> str:
    return deployment.service_name.replace("_", "-")


def _cluster_domain() -> str:
    return "devops.local"


def _render_manifests(project: Project, deployment: Deployment, image_ref: str) -> list[Path]:
    """Render deployment/service/ingress manifests for a project namespace."""
    from jinja2 import Environment, FileSystemLoader

    env = Environment(
        loader=FileSystemLoader(Path(__file__).resolve().parents[1] / "renderers" / "templates" / "k8s"),
        keep_trailing_newline=True,
    )
    name = _deployment_name(deployment)
    namespace = project.name
    context = {
        "name": name,
        "namespace": namespace,
        "image": image_ref,
        "port": deployment.port,
        "replicas": deployment.replicas,
        "strategy": deployment.strategy,
        "domain": _cluster_domain(),
    }
    # Namespace-mode projects have no Terraform workspace; their manifests
    # must not be dumped into one (docs/TODO.md §8 item 1). A missing spec
    # falls back to the historical VM layout so the renderer stays usable in
    # isolation.
    mode = "vm"
    if isinstance(project.infra_spec, dict):
        mode = project.infra_spec.get("mode", "vm")
    out_dir = deployment_manifests_dir(project.id, mode)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    # Progressive delivery (docs/TODO.md Task 5.2): a canary/bluegreen
    # deployment renders an Argo Rollout + SLO AnalysisTemplate instead of
    # the plain Deployment.
    base = ["service.yaml.j2", "ingress.yaml.j2"]
    if deployment.strategy == "deployment":
        templates = ["deployment.yaml.j2", *base]
    else:
        templates = ["rollout.yaml.j2", "analysis.yaml.j2", *base]
    for template in templates:
        path = out_dir / template.replace(".j2", "")
        path.write_text(env.get_template(template).render(**context))
        written.append(path)
    return written


def kubectl(args: list[str], project: Project, on_line=None) -> SandboxResult:
    kubeconfig = Path(settings.kubeconfig_path)
    mounts = []
    if kubeconfig.exists():
        mounts.append((kubeconfig, "/kube/config", True))
    return run_sandbox(
        SandboxRun(
            command=["kubectl", *args],
            mounts=mounts,
            env={"KUBECONFIG": "/kube/config"},
            network_enabled=True,
            timeout_seconds=300,
            on_line=on_line,
        )
    )


def kubectl_apply(manifest_paths: list[Path], project: Project, on_line=None) -> None:
    kubeconfig_mounts = []
    if Path(settings.kubeconfig_path).exists():
        kubeconfig_mounts = [(Path(settings.kubeconfig_path), "/kube/config", True)]

    namespaces = run_sandbox(
        SandboxRun(
            command=["kubectl", "get", "namespace", project.name, "-o", "name"],
            mounts=kubeconfig_mounts,
            env={"KUBECONFIG": "/kube/config"},
            network_enabled=True,
            timeout_seconds=120,
        )
    )
    if namespaces.exit_code != 0:
        kubectl(["create", "namespace", project.name], project, on_line=on_line)
    for manifest in manifest_paths:
        result = run_sandbox(
            SandboxRun(
                command=["kubectl", "apply", "-f", str(manifest)],
                mounts=kubeconfig_mounts,
                env={"KUBECONFIG": "/kube/config"},
                network_enabled=True,
                timeout_seconds=300,
                on_line=on_line,
            )
        )
        _check(result)


# ---------------------------------------------------------------------------
# Node health poller (beat task)
# ---------------------------------------------------------------------------

@celery_app.task(name="controlplane.workers.tasks.beat_pulse")
def beat_pulse() -> None:
    """Stamp a Redis heartbeat so the control plane can alert if beat dies
    (OPERATIONS.md §5: "beat not heartbeating" — node status goes stale
    without appearing broken). The API's /metrics exposes the staleness."""
    import time as time_module

    import redis as redis_client

    r = redis_client.Redis.from_url(settings.redis_url, socket_connect_timeout=1, socket_timeout=1)
    r.set("controlplane:beat:pulse", time_module.time(), ex=90)


@celery_app.task(name="controlplane.workers.tasks.poll_nodes")
def poll_nodes() -> None:
    db = SessionLocal()
    try:
        from controlplane.models import Node

        nodes = db.query(Node).filter(Node.ip_address.is_not(None)).all()
        if not nodes:
            # No nodes to poll — skip this iteration ( §8 item 7 )
            return
        for node in nodes:
            reachable = _port_open(str(node.ip_address), 22, timeout=2)
            node.status = "running" if reachable else "unreachable"
            from datetime import datetime

            node.last_seen_at = datetime.now(UTC) if reachable else node.last_seen_at
        db.commit()
    finally:
        db.close()


def _port_open(host: str, port: int, timeout: float = 2) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# TTL reaper (beat task) — docs/TODO.md Task 2.2
# ---------------------------------------------------------------------------

# Ephemeral environments that are never destroyed are just expensive permanent
# ones. This is the task that makes the cost bounded.
EXPIRY_WARNING_MINUTES = 60


@celery_app.task(name="controlplane.workers.tasks.reap_expired_projects")
def reap_expired_projects() -> dict:
    """Destroy projects whose TTL has elapsed; warn those about to expire."""
    from datetime import datetime, timedelta

    db = SessionLocal()
    warned = 0
    reaped = 0
    try:
        now = datetime.now(UTC)

        # Warn first, so the UI can show a countdown before anything is lost.
        soon = now + timedelta(minutes=EXPIRY_WARNING_MINUTES)
        for project in (
            db.query(Project)
            .filter(
                Project.auto_destroy.is_(True),
                Project.expiry_warned.is_(False),
                Project.status == "ready",
                Project.expires_at.is_not(None),
                Project.expires_at <= soon,
                Project.expires_at > now,
            )
            .all()
        ):
            project.expiry_warned = True
            warned += 1
        db.commit()

        expired = (
            db.query(Project)
            .filter(
                Project.auto_destroy.is_(True),
                Project.status.in_(("ready", "failed")),
                Project.expires_at.is_not(None),
                Project.expires_at <= now,
            )
            # Multi-worker safety (§7 item 7): two reapers racing on the
            # same beat schedule would both see the expired row before
            # either queues the destroy. FOR UPDATE SKIP LOCKED makes one
            # claim each row; the loser skips to the next project and can
            # never double-queue a destroy.
            .with_for_update(skip_locked=True)
            .all()
        )

        for project in expired:
            # System scope: the reaper acts on every expired project, not on
            # behalf of any one user — the TTL contract was agreed at creation.
            repo = ProjectRepository(db, Scope.system())
            # Never queue a second destroy for a project already being torn
            # down — the reaper runs repeatedly and must be idempotent.
            if repo.get_active_provision_job(project.id):
                db.rollback()
                continue

            # The destroy Job is created inline, in THIS transaction — not via
            # queue_destroy(), which commits on a second connection. That
            # helper's Job INSERT performs an FK check taking FOR KEY SHARE
            # on the project row this transaction already holds FOR UPDATE;
            # the two connections wait on each other and PostgreSQL cannot
            # detect the cycle (one side is idle-in-transaction). Inline, the
            # FK check re-enters the same transaction and is granted at once,
            # and the Job row + celery task id + status flip commit together —
            # no orphaned "destroying" project and no destroy without a Job.
            job = Job(project_id=project.id, type="destroy", status="queued")
            _stamp_request_id(job)
            db.add(job)
            # Materialize job.id (client-side default) before the broker call.
            db.flush()
            project.status = "destroying"
            result = destroy_task.apply_async(
                args=[
                    str(job.id),
                    str(project.id),
                    project.workspace_path or "",
                    project.name,
                    str(project.owner_id),
                ]
            )
            job.celery_task_id = result.id
            # Commit per project: releases this row's lock so a concurrent
            # reaper picks up the next expired project instead of starving.
            db.commit()

            _append_log(
                str(job.id),
                f"Automatic cleanup: TTL of {project.ttl_hours}h elapsed "
                f"(expired at {project.expires_at.isoformat()}).",
            )
            _audit_system(db, project, "project.reaped")
            reaped += 1

        # Warned flags are plain idempotent updates; commit whatever remains.
        db.commit()

        return {"warned": warned, "reaped": reaped}
    finally:
        db.close()


def _audit_system(db: Session, project: Project, action: str) -> None:
    """Record an action the platform took on its own initiative."""
    from controlplane.repositories.users import AuditLogRepository

    AuditLogRepository(db).record(
        user_id=project.owner_id,
        action=action,
        resource_type="project",
        resource_id=str(project.id),
        ip_address="system",
        detail={"reason": "ttl_expired", "ttl_hours": project.ttl_hours},
    )
    db.commit()


# ---------------------------------------------------------------------------
# Warm pool maintenance (beat task) — docs/TODO.md Task 2.5
# ---------------------------------------------------------------------------

@celery_app.task(name="controlplane.workers.tasks.replenish_pool")
def replenish_pool() -> dict:
    """Top the warm pool back up to its configured size.

    Claimed clusters are not returned to the pool — they now belong to a
    project — so the pool must be refilled with freshly provisioned ones.
    """
    from controlplane.core.pool import spec_hash
    from controlplane.core.presets import expand_preset
    from controlplane.schemas.spec import InfraSpec

    targets = settings.warm_pool_targets
    if not targets:
        return {"created": 0}

    db = SessionLocal()
    created = 0
    try:
        for preset_name, target in targets.items():
            spec = InfraSpec.model_validate(expand_preset(preset_name, "pool-template"))
            fingerprint = spec_hash(spec)
            available = (
                db.query(PooledCluster)
                .filter(
                    PooledCluster.spec_hash == fingerprint,
                    PooledCluster.status.in_(("available", "warming")),
                )
                .count()
            )
            for _ in range(max(0, target - available)):
                cluster = PooledCluster(
                    spec_hash=fingerprint,
                    workspace_path=str(project_workspace(uuid.uuid4())),
                    status="warming",
                )
                db.add(cluster)
                created += 1
        db.commit()
        return {"created": created}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------

def revoke_job(job: Job) -> None:

    try:
        if job.celery_task_id:
            celery_app.control.revoke(job.celery_task_id, terminate=True)
    except Exception:  # noqa: BLE001
        pass
    with SessionLocal() as db:
        current = db.get(Job, job.id)
        if current:
            current.status = "cancelled"
            from datetime import datetime

            current.finished_at = datetime.now(UTC)
            db.commit()
