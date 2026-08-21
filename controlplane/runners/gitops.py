"""Publish rendered tenant manifests into the platform's manifest repository.

ArgoCD can only sync from a git source, and the platform deliberately stores
no tenant *source* code — so what gets committed here is never the tenant's
repository. It is the handful of YAML files the platform rendered itself
(Deployment/Rollout, Service, Ingress), after the image they reference has
already passed the vulnerability gate. The tenant cannot write into this
repository; only the worker can, and only under the path belonging to the
project it is currently deploying.

Everything runs in the sandbox, for the same reason cloning does: the push
credential must not reach the ``docker run`` argv, and git must never be able
to prompt on stdin and hang a build slot until the timeout.
"""

from __future__ import annotations

import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from controlplane.core.config import settings
from controlplane.core.git_credentials import ASKPASS_SCRIPT
from controlplane.renderers.argocd import manifest_path
from controlplane.runners.sandbox import SandboxError, SandboxResult, SandboxRun, run_sandbox


class GitOpsError(RuntimeError):
    """Publishing failed; the caller must not report the deploy as shipped."""


@dataclass(frozen=True)
class GitOpsConfig:
    repo_url: str
    branch: str
    username: str
    password: str

    @classmethod
    def from_settings(cls) -> GitOpsConfig:
        return cls(
            repo_url=settings.gitops_repo_url,
            branch=settings.gitops_branch,
            username=settings.gitops_username,
            password=settings.gitops_password,
        )


def _git(args: list[str], workspace: Path, config: GitOpsConfig, askpass: Path, on_line=None) -> SandboxResult:
    return run_sandbox(
        SandboxRun(
            command=["git", "-C", "/workspace/repo", *args],
            workspace=workspace,
            writable_paths=["repo"],
            network_enabled=True,
            network=settings.registry_network,
            timeout_seconds=180,
            on_line=on_line,
            env={
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_ASKPASS": str(askpass),
                # A commit needs an identity, and git refuses rather than
                # inventing one. Attributing it to the platform (not to the
                # tenant) is accurate: the platform rendered these files.
                "GIT_AUTHOR_NAME": "controlplane",
                "GIT_AUTHOR_EMAIL": "controlplane@platform.local",
                "GIT_COMMITTER_NAME": "controlplane",
                "GIT_COMMITTER_EMAIL": "controlplane@platform.local",
            },
            secret_env={"GIT_USERNAME": config.username, "GIT_PASSWORD": config.password},
        )
    )


def publish_manifests(
    project_id: uuid.UUID,
    service_name: str,
    manifests: list[Path],
    message: str,
    config: GitOpsConfig | None = None,
    on_line=None,
) -> str:
    """Commit ``manifests`` under this service's path and push. Returns the sha.

    The service's directory is emptied before the new files are copied in, so
    a manifest the renderer stopped producing (a Rollout after a switch back
    to a plain Deployment) disappears from git and is then pruned from the
    cluster. Merging into whatever was there before would leave both objects
    live and fighting over the same pods.
    """
    config = config or GitOpsConfig.from_settings()
    if not config.repo_url:
        raise GitOpsError("GITOPS_REPO_URL is not configured.")

    root = Path("/tmp/ctl-gitops") / uuid.uuid4().hex
    (root / "repo").mkdir(parents=True, exist_ok=True)
    askpass = root / "askpass.sh"
    askpass.write_text(ASKPASS_SCRIPT)
    askpass.chmod(0o700)

    try:
        clone = run_sandbox(
            SandboxRun(
                command=[
                    "git", "clone", "--depth", "1", "--branch", config.branch,
                    config.repo_url, "/workspace/repo",
                ],
                workspace=root,
                writable_paths=["repo"],
                network_enabled=True,
                network=settings.registry_network,
                timeout_seconds=180,
                on_line=on_line,
                env={"GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": str(askpass)},
                secret_env={"GIT_USERNAME": config.username, "GIT_PASSWORD": config.password},
            )
        )
        if clone.exit_code != 0:
            raise GitOpsError(f"could not clone the manifest repository: {clone.output[-400:]}")

        target = root / "repo" / manifest_path(project_id, service_name)
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)
        for manifest in manifests:
            # The rendered Secret holds the tenant's values in the clear. It
            # is applied directly and never committed: a git repository keeps
            # history forever, so a secret written here would survive every
            # later rotation and stay readable to anyone who can read the
            # repo. This is the one manifest GitOps does not manage.
            if manifest.name == "secret.yaml":
                continue
            shutil.copy2(manifest, target / manifest.name)

        _git(["add", "--all", "."], root, config, askpass, on_line)
        commit = _git(["commit", "-m", message], root, config, askpass, on_line)
        if commit.exit_code != 0:
            # "nothing to commit" is a redeploy of an identical spec — the
            # image tag pins the commit, so an unchanged render means the
            # cluster is already at the requested state. Not an error.
            if "nothing to commit" in commit.output:
                head = _git(["rev-parse", "HEAD"], root, config, askpass)
                return head.output.strip()
            raise GitOpsError(f"could not commit manifests: {commit.output[-400:]}")

        push = _git(["push", "origin", f"HEAD:{config.branch}"], root, config, askpass, on_line)
        if push.exit_code != 0:
            raise GitOpsError(f"could not push manifests: {push.output[-400:]}")

        head = _git(["rev-parse", "HEAD"], root, config, askpass)
        return head.output.strip()
    except SandboxError as exc:
        raise GitOpsError(str(exc)) from exc
    finally:
        shutil.rmtree(root, ignore_errors=True)
