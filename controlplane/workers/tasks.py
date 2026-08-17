"""Celery tasks: provisioning, destroy, deployment pipeline, and scanning.

Workers never execute user-supplied code directly — every external tool runs
through the sandbox (docs/PLATFORM_SPEC.md §7.2). Job logs are scrubbed
before they are written to the database (§7.4).
"""

import contextlib
import json
import os
import shutil
import socket
import tempfile
import time
import uuid
from datetime import UTC
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from controlplane.core.config import settings
from controlplane.core.git_credentials import ASKPASS_SCRIPT, credential_for_repo
from controlplane.core.kubeconfigs import get_kubeconfig, store_kubeconfig, transfer_kubeconfig
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
from controlplane.core.validation import k8s_namespace
from controlplane.db import SessionLocal
from controlplane.models import (
    ACTIVE_STATUSES,
    Deployment,
    Job,
    PooledCluster,
    Project,
    Scan,
    Team,
)
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


def _check(result, hint: str | None = None) -> None:
    """Raise on a failed sandbox run.

    `hint` is a human-readable explanation for a known failure mode. The raw
    output is what reaches the user's screen otherwise, and pasting docker's
    "unable to evaluate symlinks in Dockerfile path: lstat ..." at someone who
    simply has no Dockerfile tells them nothing they can act on.
    """
    if result.timed_out:
        raise RuntimeError(hint or f"command timed out: {result.output[-500:]}")
    if result.exit_code != 0:
        if hint:
            raise RuntimeError(hint)
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

        # terraform apply returning only means libvirt started the domain,
        # not that the guest finished booting and cloud-init applied the SSH
        # key — racing straight into ansible-playbook here made every VM-mode
        # provision fail with "Permission denied (publickey)" or plain
        # unreachable, indistinguishable from a real misconfiguration.
        _append_log(job_id, "Waiting for nodes to accept SSH connections...")
        _wait_for_ssh(job_id, list(node_ips.values()))

        _append_log(job_id, "[4/4] ansible-playbook configure")
        key = user_ssh_private_key(uuid.UUID(user_id))
        # sshd starts long before cloud-init finishes creating the user and
        # installing the authorized key — and cloud-init itself runs a full
        # package_upgrade on first boot, which can take most of the ~10
        # minutes the UI already tells users to expect for VM mode, longer
        # under host memory pressure. Retry the (idempotent) playbook run
        # rather than failing the whole provision on a transient "Permission
        # denied" from a guest that's still finishing first-boot. Budgeted to
        # stay under the task's own hard time limit (provision_timeout_seconds
        # + 120s in celery_app.py).
        attempts = 20
        retry_delay_seconds = 30
        for attempt in range(1, attempts + 1):
            result = ansible_playbook(ws, key, on_line=on_line)
            if result.exit_code == 0:
                break
            if attempt == attempts:
                _check(result)
            _append_log(
                job_id,
                f"ansible-playbook attempt {attempt}/{attempts} failed "
                f"(guest likely still finishing cloud-init) — retrying in {retry_delay_seconds}s",
            )
            time.sleep(retry_delay_seconds)

        # Dedicated-cluster-per-tenant (multi-tenancy Phase 3): the master
        # role fetches this cluster's own admin kubeconfig back into the
        # workspace. Move it into Vault and scrub the plaintext copy —
        # workspace_path is not a secret store.
        kubeconfig_path = ws / "kubeconfig.yaml"
        if kubeconfig_path.exists() and kubeconfig_path.stat().st_size > 0:
            store_kubeconfig(uuid.UUID(project_id), kubeconfig_path.read_text())
            kubeconfig_path.write_text("")
            _append_log(job_id, "Stored dedicated cluster credential.")

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
    project = db.get(Project, uuid.UUID(project_id))
    ns = k8s_namespace(project.id)
    _append_log(job_id, "[1/2] rendering namespace, quota, limits and network policy")
    manifest = render_namespace(spec, ns, ws)

    _append_log(job_id, "[2/2] applying to the shared cluster")
    kubectl_apply([manifest], project, on_line)

    # There are no VMs in this mode; the recorded nodes describe the quota
    # rather than machines, so they are marked ready without an IP.
    from controlplane.models import Node

    for node in db.query(Node).filter(Node.project_id == uuid.UUID(project_id)).all():
        node.status = "running"
    db.commit()

    _mark_ready(db, project_id)
    _mark_job(db, uuid.UUID(job_id), "succeeded")
    _append_log(job_id, f"Namespace {ns} ({spec.project}) ready.")


def _adopt_pooled_cluster(
    db: Session, job_id: str, project_id: str, user_id: str, pooled: PooledCluster
) -> None:
    """Hand a pre-warmed cluster to the project instead of provisioning."""
    _append_log(job_id, "Claimed a pre-warmed cluster from the pool.")

    project = db.get(Project, uuid.UUID(project_id))
    if project is not None:
        project.workspace_path = pooled.workspace_path
        db.commit()

    # The pool warms clusters under their own PooledCluster id (no project
    # exists yet at warm time); re-key the credential to the claiming
    # project now that one does.
    transfer_kubeconfig(pooled.id, uuid.UUID(project_id))

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
            # Computed from project_id, never taken from the caller-supplied
            # project_name: that string is only unique per team (Phase 1 of
            # the multi-tenancy plan), so trusting it here would let deleting
            # project A also delete a same-named project B's namespace. It
            # also works even when the Project row is already gone (the
            # full-delete path removes it before this task runs).
            ns = k8s_namespace(uuid.UUID(project_id))
            _append_log(job_id, f"deleting namespace {ns} ({project_name})")
            result = kubectl(
                ["delete", "namespace", ns, "--ignore-not-found", "--wait=true"],
                project,
                on_line=on_line,
            )
            if result.exit_code != 0:
                _set_project_status(db, project_id, "failed")
                _mark_job(db, uuid.UUID(job_id), "failed", result.output[-800:])
                return
            _remove_workspace(ws, job_id)
            _set_project_status(db, project_id, "destroyed")
            _mark_job(db, uuid.UUID(job_id), "succeeded")
            _append_log(job_id, f"Namespace {ns} ({project_name}) removed.")
            return

        if ws.exists():
            _append_log(job_id, "terraform destroy")
            result = terraform_destroy(ws, on_line=on_line)
            if result.exit_code != 0:
                _append_log(job_id, f"destroy exit {result.exit_code}")
                _set_project_status(db, project_id, "failed")
                _mark_job(db, uuid.UUID(job_id), "failed", result.output[-800:])
                return
            _remove_workspace(ws, job_id)
        else:
            _append_log(job_id, "No workspace found — nothing to destroy.")

        # The cluster this credential pointed at no longer exists; leaving
        # it in Vault would be a stale admin credential with nothing left to
        # scope it once the project id is reused for anything else.
        from controlplane.core.kubeconfigs import delete_kubeconfig

        delete_kubeconfig(uuid.UUID(project_id))

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
    """Update a project's status, tolerating the row having been deleted.

    ``DELETE /projects/{id}`` queues the destroy job *and* deletes the row,
    so this worker races the API: the Project this session loaded at the top
    of ``destroy_task`` can be gone by the time we get here. Without the
    expire, ``db.get`` returns the stale identity-map copy, the UPDATE
    matches 0 rows, and SQLAlchemy poisons the session — leaving the job
    stuck in "running" forever even though the teardown itself succeeded.
    """
    db.expire_all()
    project = db.get(Project, uuid.UUID(project_id))
    if project is None:
        return
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
            # Defence in depth against the dispatch-before-commit race the
            # router now avoids: returning silently here used to leave the job
            # "running" forever with nothing to explain it.
            _mark_job(db, uuid.UUID(job_id), "failed", f"scan {scan_id} not found")
            return
        repo.set_result(scan, "running")
        db.commit()

        on_line = _log_lines(job_id)
        # Scans clone the same private repositories deployments do, so they
        # resolve the same team credential. Taken from the scan's own project
        # rather than anything the caller supplied: that is what stops one
        # tenant's job from reaching another tenant's credential.
        scan_project = db.get(Project, scan.project_id) if scan.project_id else None
        scan_team_id = scan_project.team_id if scan_project is not None else None
        # Tenant source lands on the shared control-plane host only for as long
        # as the scan needs it. Cleanup used to sit inline after each scanner
        # call, so any failure between clone and cleanup — a scanner crash, a
        # parse error, a revoked token mid-run — left the checkout behind for
        # good. Tracked here and removed in `finally` instead.
        cloned: Path | None = None
        try:
            if tool == "trivy":
                # Same reasoning as the pre-deploy gate: the sandbox has no
                # docker socket, deliberately, so Trivy's default daemon
                # lookup cannot work and an on-demand image scan failed with
                # "failed to connect to the docker API". Read the image from
                # the registry instead. Only the deploy path was fixed
                # before, which left this one broken.
                result = run_trivy(
                    _registry_scan_ref(target),
                    on_line=on_line,
                    from_registry=True,
                    network=settings.registry_network,
                    insecure=settings.registry_insecure,
                )
                from controlplane.parsers.trivy_parser import parse_trivy

                parsed = parse_trivy(result.stdout)
                if not _is_usable_trivy_report(result.stdout):
                    raise RuntimeError(
                        f"Trivy could not scan {target}. Exit {result.exit_code}: "
                        f"{(result.stdout or '')[-400:]}"
                    )
            elif tool == "gitleaks":
                cloned = _clone_repo(target, job_id, on_line, team_id=scan_team_id)
                result = run_gitleaks(cloned, on_line=on_line)
                raw = result.artifact_path
                text = Path(raw).read_text() if raw and Path(raw).exists() else "[]"
                from controlplane.parsers.gitleaks_parser import parse_gitleaks

                parsed = parse_gitleaks(text)
            else:  # pip_audit
                cloned = _clone_repo(target, job_id, on_line, team_id=scan_team_id)
                requirements = _find_requirements(cloned)
                result = run_pip_audit(requirements, on_line=on_line)
                from controlplane.parsers.pip_audit_parser import parse_pip_audit

                parsed = parse_pip_audit(result.stdout)

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
            if cloned is not None:
                _purge_path(cloned.parent, job_id)
    finally:
        db.close()


def _safe_json(text: str) -> dict:
    try:
        data = json.loads(text)
        return data if isinstance(data, dict | list) else {"raw": text[:5000]}
    except json.JSONDecodeError:
        return {"raw": text[:5000]}


def _logger():
    import logging

    return logging.getLogger("controlplane.workers")


def _remove_workspace(ws: Path, job_id: str) -> None:
    """Delete a project workspace, and refuse to delete anything else.

    `queue_destroy` passes `workspace or ""` (tasks.py, queue_destroy), so a
    project whose `workspace_path` was never set — a draft, or one adopted
    from the warm pool — arrives here as `Path("")`. That is `.`, and
    `shutil.rmtree(".", ignore_errors=True)` deletes the worker's current
    working directory: on this instance, the control plane's own checkout,
    silently, because errors were ignored.

    A workspace always lives under `settings.workspace_root`. Anything else is
    a bug in the caller, so it is refused and recorded rather than obeyed.
    """
    root = Path(settings.workspace_root).resolve()
    try:
        target = ws.resolve()
    except OSError:
        _append_log(job_id, f"refusing to remove unresolvable workspace path {ws!r}")
        return

    if not str(ws).strip():
        _append_log(job_id, "no workspace recorded for this project — nothing to remove")
        return
    if target == root or root not in target.parents:
        _append_log(
            job_id,
            f"refusing to remove {target}: outside the workspace root {root}",
        )
        _logger().error("destroy.unsafe_workspace path=%s root=%s", target, root)
        return

    shutil.rmtree(target, ignore_errors=True)
    _append_log(job_id, f"workspace {target} removed")


def _clone_repo(
    repo_url: str,
    job_id: str,
    on_line,
    branch: str | None = None,
    team_id=None,
) -> Path:
    """Validate + clone into a fresh host-visible workspace. Returns the repo dir.

    The workspace root is mounted into the sandbox with the repo subdir
    writable, so the clone survives on the host for later scan/deploy steps.
    """
    validate_repo_url(repo_url)
    root = Path("/tmp/ctl-repos") / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=True)
    (root / "repo").mkdir(parents=True, exist_ok=True)
    target = root / "repo"

    command = ["git", "clone", "--depth", "1"]
    if branch:
        # The branch was ignored here entirely: a deployment pinned to
        # "develop" cloned the repository's default branch and shipped it
        # under the develop label. Silently deploying code the user did not
        # ask for is worse than failing, so the branch is now explicit and a
        # missing one is an error.
        command += ["--branch", branch]
    command += [repo_url, str(target)]

    # Without this git blocks on stdin asking for a username whenever the
    # repository is private or misspelt, and the job sits there until the
    # sandbox timeout kills it — minutes of a build slot spent on a failure
    # that is knowable immediately.
    env = {"GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "/bin/true"}
    secret_env: dict[str, str] = {}

    credential = credential_for_repo(str(team_id), repo_url) if team_id else None
    if credential is not None:
        # The token is handed to git through an askpass helper rather than
        # embedded in the URL. A URL credential is written by git into
        # .git/config inside the checkout, and the next pipeline step builds
        # an image from that directory — a `COPY . .` would bake the tenant's
        # token into an image and push it to a registry.
        askpass = root / "askpass.sh"
        askpass.write_text(ASKPASS_SCRIPT)
        askpass.chmod(0o700)
        env["GIT_ASKPASS"] = str(askpass)
        # Routed through secret_env so the value never reaches the docker run
        # argv, which is readable by any local user through /proc.
        secret_env = credential.askpass_env()
        _append_log(job_id, "using the team's configured git credential")

    _append_log(job_id, f"cloning {repo_url} ({branch or 'default branch'})")
    result = run_sandbox(
        SandboxRun(
            command=command,
            workspace=root,
            writable_paths=["repo"],
            network_enabled=True,
            timeout_seconds=settings.scan_timeout_seconds,
            on_line=on_line,
            env=env,
            secret_env=secret_env,
        )
    )
    _check(result, _clone_hint(result, repo_url, branch, authenticated=credential is not None))

    # Drop the git metadata before anything builds from this directory. It
    # carries the remote configuration and, for an authenticated clone, is
    # where a credential would live; it has no business inside a container
    # image either way. A tenant Dockerfile doing `COPY . .` would otherwise
    # copy it in and the image gets pushed to a registry.
    #
    # Checked rather than assumed: this used to be an ignore_errors rmtree that
    # could not remove root-owned files and said nothing about it.
    if not _purge_path(target / ".git", job_id):
        raise RuntimeError(
            "Could not remove .git from the checkout before building. Refusing to "
            "continue, because the build context would carry git metadata — and, for "
            "an authenticated clone, the credential used to fetch it — into the image."
        )
    (root / "askpass.sh").unlink(missing_ok=True)
    return target


def _purge_path(path: Path, job_id: str | None = None) -> bool:
    """Delete ``path`` even though the sandbox created it as root.

    The sandbox runs containers as uid 0, so everything a clone or build
    writes is owned by root while the control plane runs unprivileged. A plain
    ``shutil.rmtree`` therefore fails with EPERM, and because these calls
    passed ``ignore_errors=True`` it failed *silently*: tenant checkouts
    accumulated in /tmp indefinitely (4.8 MB across 49 directories on this
    instance) and ``.git`` survived into the image build.

    Removal happens inside a container for the same reason the files exist
    there. ``rm`` is invoked with an argv, never a shell string, because the
    repository path is attacker-influenced and a shell would make it
    injectable.
    """
    path = Path(path)
    if not path.exists():
        return True

    # Try unprivileged first: nothing to gain from a container when the files
    # are ours (a failed clone before the container wrote anything).
    shutil.rmtree(path, ignore_errors=True)
    if not path.exists():
        return True

    parent = path.parent
    try:
        run_sandbox(
            SandboxRun(
                # The workspace is bind-mounted at its own host path, so the
                # container sees the same absolute path we do.
                command=["rm", "-rf", str(path)],
                workspace=parent,
                workspace_writable=True,
                network_enabled=False,
                timeout_seconds=120,
            )
        )
    except Exception:  # noqa: BLE001
        # Cleanup must never be the reason a job reports failure.
        pass

    if path.exists():
        message = f"could not remove {path}; it holds tenant data and is now orphaned"
        if job_id:
            _append_log(job_id, message)
        return False
    return True


def _registry_scan_ref(image_ref: str) -> str:
    """Rewrite a pushed image reference the way a sandbox can reach it.

    The control plane pushes to `settings.registry`, an address published on
    the host. Inside a sandbox container that same address resolves to the
    container itself, so the image has to be named by its address on the
    registry's own network.
    """
    if settings.registry_internal and image_ref.startswith(f"{settings.registry}/"):
        return image_ref.replace(settings.registry, settings.registry_internal, 1)
    return image_ref


def _is_usable_trivy_report(raw: str) -> bool:
    """True only when the output is a report Trivy actually produced.

    Distinguishes "scanned, nothing found" from "never scanned". The former
    is a real report carrying a Results key; the latter is empty output or a
    plain error string, which json.loads either rejects or turns into
    something without Results.
    """
    import json as _json

    try:
        data = _json.loads(raw or "")
    except (ValueError, TypeError):
        return False
    return isinstance(data, dict) and "Results" in data


def _clone_hint(result, repo_url: str, branch: str | None, authenticated: bool = False) -> str | None:
    """Translate git's failure into something the person who typed the URL can act on."""
    output = (result.output or "").lower()
    if "could not read username" in output or "authentication failed" in output:
        if authenticated:
            # Telling someone to make the repository public when they have
            # already configured a credential sends them the wrong way.
            return (
                f"Cannot read {repo_url} with the team's git credential. The token may be "
                "expired, may not grant access to this repository, or may lack read access "
                "to its contents."
            )
        return (
            f"Cannot read {repo_url}: the repository is private or does not exist. "
            "Either make it public, or add a git credential for your team so the platform "
            "can read it."
        )
    if "remote branch" in output and "not found" in output:
        return f"Branch '{branch}' does not exist in {repo_url}."
    if "repository not found" in output or "not found" in output and "fatal" in output:
        return f"Repository {repo_url} not found."
    return None


def _find_requirements(repo: Path) -> Path:
    candidates = [repo / "requirements.txt", repo / "app" / "requirements.txt", repo / "requirements" / "base.txt"]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError("No requirements.txt found in repository.")


# ---------------------------------------------------------------------------
# Deployment pipeline
# ---------------------------------------------------------------------------

class DeployAlreadyRunning(RuntimeError):
    """A deploy for this deployment is already queued or running.

    Carries the job so callers can point the user at the run in progress
    rather than at a bare error.
    """

    def __init__(self, job: Job) -> None:
        super().__init__("A deploy for this service is already in progress.")
        self.job = job


def active_deploy_job(db: Session, deployment_id: uuid.UUID) -> Job | None:
    """The deploy job currently in flight for this deployment, if any."""
    return db.scalars(
        select(Job)
        .where(
            Job.deployment_id == deployment_id,
            Job.type == "deploy",
            Job.status.in_(ACTIVE_STATUSES),
        )
        .order_by(Job.created_at.desc())
        .limit(1)
    ).first()


def queue_deploy(deployment: Deployment, project: Project, user_id: uuid.UUID) -> Job:
    """Queue a deploy, refusing to start a second one for the same deployment.

    Two concurrent deploys of one service build from different commits, push
    under the same tag prefix and apply the same manifests, so the rollout
    that survives is decided by which worker happens to finish last.
    """
    db = SessionLocal()
    try:
        running = active_deploy_job(db, deployment.id)
        if running is not None:
            raise DeployAlreadyRunning(running)

        job = Job(project_id=project.id, deployment_id=deployment.id, type="deploy", status="queued")
        _stamp_request_id(job)
        db.add(job)
        try:
            db.flush()
            db.commit()
        except IntegrityError:
            # Two requests both passed the check above before either inserted.
            # The partial unique index is what actually decides; the loser
            # reports the winner.
            db.rollback()
            running = active_deploy_job(db, deployment.id)
            if running is None:
                raise
            raise DeployAlreadyRunning(running) from None
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
            args=[str(job.id), _deployment_name(deployment), str(project.id), str(user_id)]
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
        # Namespaced by team, not just project name: two teams can each have
        # a project called "staging" (Phase 1), and an image tag collision
        # between them would mean one tenant's build silently overwrites —
        # or gets served — another tenant's image.
        team = db.get(Team, project.team_id)
        image_ref = f"{settings.registry}/{team.slug}/{project.name}-{deployment.service_name}:commit-{uuid.uuid4().hex[:8]}"

        # Same rule as scan_task: the tenant's checkout is transient, so its
        # removal must not depend on the build, scan and push all succeeding.
        cloned: Path | None = None
        try:
            _append_log(job_id, "[1/7] cloning repository")
            cloned = _clone_repo(
                deployment.repo_url, job_id, on_line,
                branch=deployment.branch, team_id=project.team_id,
            )

            # Check this before spending a build slot on it. docker's own
            # complaint ("unable to evaluate symlinks in Dockerfile path:
            # lstat .../repo/Dockerfile") names a path inside a temp directory
            # the user has never heard of and cannot inspect.
            if not (cloned / "Dockerfile").is_file():
                raise RuntimeError(
                    f"No Dockerfile at the root of {deployment.repo_url} "
                    f"(branch {deployment.branch}). This platform builds a service from a "
                    "Dockerfile in the repository root — add one and deploy again."
                )

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

            _append_log(job_id, "[3/7] pushing image to registry")
            push = run_sandbox(
                SandboxRun(
                    command=["docker", "push", image_ref],
                    requires_docker_daemon=True,
                    timeout_seconds=600,
                    on_line=on_line,
                    env={"REGISTRY_USER": settings.registry_user},
                    secret_env={"REGISTRY_PASSWORD": settings.registry_password},
                )
            )
            _check(push)

            _append_log(job_id, "[4/7] trivy scan + gate")
            repo.set_status(deployment, "scanning")
            db.commit()
            # Scan the image that was just pushed, addressed the way a
            # sandbox can reach it: the control plane pushes to
            # settings.registry (published on the host), which resolves to the
            # sandbox container itself from inside one.
            scan_ref = _registry_scan_ref(image_ref)
            trivy = run_trivy(
                scan_ref,
                on_line=on_line,
                from_registry=True,
                network=settings.registry_network,
                insecure=settings.registry_insecure,
            )
            from controlplane.parsers.trivy_parser import parse_trivy

            # The gate must fail closed. `parse_trivy` returns an empty result
            # for output it cannot read, which is indistinguishable from a
            # clean image, so a scanner that never ran produced gate == 0 and
            # the image shipped as if it had passed. That is exactly what
            # happened here: trivy could not reach the Docker socket, logged
            # "failed to connect to the docker API", and the deployment went
            # live unscanned while the UI promised the opposite.
            #
            # An image whose vulnerabilities are unknown is not an image known
            # to be safe, so treat an unusable scan as a block.
            if trivy.timed_out or trivy.exit_code != 0:
                # Trivy downloads its vulnerability database on first use, and
                # two scans starting together can collide over it. That is a
                # transient failure, and blocking a deployment over it while
                # an identical image sails through minutes later is not a
                # security decision — it is a coin toss. Retry once; a real
                # failure still blocks, because the gate stays fail-closed.
                _append_log(job_id, f"trivy exited {trivy.exit_code} — retrying once")
                time.sleep(5)
                trivy = run_trivy(
                    scan_ref,
                    on_line=on_line,
                    from_registry=True,
                    network=settings.registry_network,
                    insecure=settings.registry_insecure,
                )

            if trivy.timed_out or trivy.exit_code != 0:
                repo.set_status(deployment, "blocked", image_ref=image_ref)
                db.commit()
                raise RuntimeError(
                    "Image could not be scanned, so it was not deployed. "
                    # RawResult carries `stdout`; there is no `output`. Reading
                    # the wrong attribute raised AttributeError while building
                    # this very message, which replaced the real reason with a
                    # type error on six deployments.
                    f"Trivy exited {trivy.exit_code}: {(trivy.stdout or '')[-400:]}"
                )

            if not _is_usable_trivy_report(trivy.stdout):
                repo.set_status(deployment, "blocked", image_ref=image_ref)
                db.commit()
                raise RuntimeError(
                    "Image could not be scanned, so it was not deployed: "
                    "Trivy produced no readable report."
                )

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
            ns = k8s_namespace(project.id)
            rollout = kubectl(
                ["rollout", "status", f"{resource}/{_deployment_name(deployment)}", f"--namespace={ns}", "--timeout=180s"],
                project, on_line=on_line,
            )
            if rollout.exit_code != 0:
                _append_log(job_id, "rollout failed — rolling back")
                kubectl(["rollout", "undo", f"{resource}/{_deployment_name(deployment)}", f"--namespace={ns}"], project, on_line=on_line)
                raise RuntimeError(f"rollout failed: {rollout.output[-500:]}")

            _append_log(job_id, "[7/7] capturing live URL")
            live_url = f"http://{_deployment_name(deployment)}.{ns}.{_cluster_domain()}"
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
            if cloned is not None:
                _purge_path(cloned.parent, job_id)
    finally:
        db.close()


@celery_app.task(name="controlplane.workers.tasks.undeploy_task")
def undeploy_task(job_id: str, deployment_name: str, project_id: str, user_id: str) -> None:
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
        project = db.get(Project, uuid.UUID(project_id))
        if project is None:
            _mark_job(db, uuid.UUID(job_id), "failed", "project record missing")
            return
        namespace = k8s_namespace(project.id)
        on_line = _log_lines(job_id)
        for kind in ("deployment", "service", "ingress"):
            kubectl(["delete", kind, deployment_name, f"--namespace={namespace}", "--ignore-not-found"], project, on_line=on_line)
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
    namespace = k8s_namespace(project.id)
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


@contextlib.contextmanager
def _kubeconfig_path(project: Project | None):
    """Resolve the kubeconfig to mount for ``project``.

    Dedicated-cluster-per-tenant (multi-tenancy Phase 3): a VM-mode project
    has its own admin credential in Vault, written there by ``provision_task``
    once its cluster exists. Namespace-mode projects share one cluster and
    have no such secret, so they — and any call made without project context
    (e.g. warm-pool bookkeeping) — fall back to the operator's static
    ``settings.kubeconfig_path``.
    """
    content = get_kubeconfig(project.id) if project is not None else None
    if content:
        fd, name = tempfile.mkstemp(suffix=".yaml", prefix="ctl-kubeconfig-")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(content)
            os.chmod(name, 0o600)
            yield Path(name)
        finally:
            os.unlink(name)
        return
    yield Path(settings.kubeconfig_path)


def kubectl(args: list[str], project: Project | None, on_line=None) -> SandboxResult:
    with _kubeconfig_path(project) as kubeconfig:
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
    ns = k8s_namespace(project.id)
    with _kubeconfig_path(project) as kubeconfig:
        kubeconfig_mounts = [(kubeconfig, "/kube/config", True)] if kubeconfig.exists() else []

        namespaces = run_sandbox(
            SandboxRun(
                command=["kubectl", "get", "namespace", ns, "-o", "name"],
                mounts=kubeconfig_mounts,
                env={"KUBECONFIG": "/kube/config"},
                network_enabled=True,
                timeout_seconds=120,
            )
        )
        if namespaces.exit_code != 0:
            kubectl(["create", "namespace", ns], project, on_line=on_line)
        for manifest in manifest_paths:
            manifest = manifest.resolve()
            result = run_sandbox(
                SandboxRun(
                    command=["kubectl", "apply", "-f", str(manifest)],
                    # `mounts=` alone doesn't cut it — the container needs the
                    # rendered manifest itself; without a workspace/mount for it,
                    # kubectl sees a path that doesn't exist inside the sandbox.
                    mounts=[*kubeconfig_mounts, (manifest, str(manifest), True)],
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


def _wait_for_ssh(job_id: str, ips: list[str], timeout_seconds: int = 240, poll_seconds: float = 5) -> None:
    """Block until every node accepts TCP connections on :22.

    A libvirt domain reporting "running" only means the guest started
    booting — cloud-init still has to run (create the user, install the SSH
    key, bring up networking) before anything can SSH in. Raises on timeout
    rather than letting the caller hit a confusing ansible failure.
    """
    deadline = time.monotonic() + timeout_seconds
    pending = set(ips)
    while pending and time.monotonic() < deadline:
        pending = {ip for ip in pending if not _port_open(ip, 22, timeout=3)}
        if pending:
            time.sleep(poll_seconds)
    if pending:
        raise RuntimeError(
            f"Timed out after {timeout_seconds}s waiting for SSH on: {', '.join(sorted(pending))} "
            "— the VM may still be booting, or cloud-init failed."
        )


# ---------------------------------------------------------------------------
# TTL reaper (beat task) — docs/TODO.md Task 2.2
# ---------------------------------------------------------------------------

# Ephemeral environments that are never destroyed are just expensive permanent
# ones. This is the task that makes the cost bounded.
EXPIRY_WARNING_MINUTES = 60


# ---------------------------------------------------------------------------
# Stale job reaper
# ---------------------------------------------------------------------------

@celery_app.task(name="controlplane.workers.tasks.reap_stale_jobs")
def reap_stale_jobs() -> dict:
    """Fail jobs whose worker died mid-task, so a project is not bricked.

    A worker that is SIGKILLed — OOM, redeploy, node eviction, or Celery's own
    hard ``task_time_limit`` — never runs its ``except`` block, so the Job row
    stays "running" forever. That is not cosmetic:
    ``get_active_provision_job`` treats queued/running provision and destroy
    jobs as an in-flight lock, so the project's provision *and* destroy
    endpoints both return 409 permanently, and ``reap_expired_projects`` skips
    it too — the environment can never be rebuilt, removed, or reaped, and
    whatever infrastructure it holds leaks for good.

    Celery kills a task at ``task_time_limit`` (provision_timeout_seconds +
    120), so a job still marked running well past that limit cannot be alive.
    The margin below is deliberately generous: failing a job that is merely
    slow would abort real work, whereas leaving a genuinely dead one costs
    only the next sweep.
    """
    from datetime import datetime, timedelta

    hard_limit = settings.provision_timeout_seconds + 120
    cutoff = datetime.now(UTC) - timedelta(seconds=hard_limit + 300)

    db = SessionLocal()
    failed = 0
    try:
        stale = (
            db.query(Job)
            .filter(
                Job.status == "running",
                Job.started_at.is_not(None),
                Job.started_at < cutoff,
            )
            .all()
        )
        for job in stale:
            job.status = "failed"
            job.finished_at = datetime.now(UTC)
            job.error_message = (
                "Worker stopped without reporting a result (killed, restarted or "
                f"timed out). Marked failed after {hard_limit + 300}s so the "
                "environment is not left locked."
            )
            failed += 1
            # A project left mid-flight by a dead worker is in an unknown
            # state; say so rather than implying the last action succeeded.
            if job.project_id is not None:
                project = db.get(Project, job.project_id)
                if project is not None and project.status in ("provisioning", "destroying"):
                    project.status = "failed"
        db.commit()
        return {"failed": failed}
    finally:
        db.close()


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
