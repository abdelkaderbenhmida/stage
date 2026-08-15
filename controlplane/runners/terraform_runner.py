"""Terraform runner built on the sandbox (docs/PLATFORM_SPEC.md §4, §7.2).

Per-project state is stored inside the rendered workspace so each project gets
its own encrypted, non-world-readable state path that is never surfaced
through the API.
"""

from collections.abc import Callable
from pathlib import Path

from controlplane.core.config import settings
from controlplane.runners.sandbox import SandboxResult, SandboxRun, run_sandbox


def _writable_workspace_paths(workspace: Path) -> list[str]:
    for relative in (".terraform", ".terraform.lock.hcl", "terraform.tfstate", "terraform.tfstate.backup"):
        path = workspace / relative
        if relative == ".terraform":
            path.mkdir(parents=True, exist_ok=True)
        elif not path.exists():
            path.touch()
    return [".terraform", ".terraform.lock.hcl", "terraform.tfstate", "terraform.tfstate.backup"]


def _terraform_run(
    workspace: Path,
    subcommand: list[str],
    env: dict[str, str],
    timeout: int,
    on_line: Callable[[str], None] | None = None,
) -> SandboxResult:
    return run_sandbox(
        SandboxRun(
            command=["terraform", *subcommand],
            workspace=workspace,
            workspace_writable=True,
            writable_paths=_writable_workspace_paths(workspace),
            env=env,
            network_enabled=True,
            timeout_seconds=timeout,
            on_line=on_line,
        )
    )


def terraform_init(
    workspace: Path,
    env: dict[str, str] | None = None,
    on_line: Callable[[str], None] | None = None,
    timeout: int = 300,
) -> SandboxResult:
    return _terraform_run(workspace, ["init", "-input=false", "-no-color"], env or {}, timeout, on_line)


def terraform_plan(
    workspace: Path,
    env: dict[str, str] | None = None,
    on_line: Callable[[str], None] | None = None,
    timeout: int = 300,
) -> SandboxResult:
    return _terraform_run(
        workspace,
        ["plan", "-input=false", "-no-color", "-var-file=terraform.tfvars"],
        env or {},
        timeout,
        on_line,
    )


def terraform_apply(
    workspace: Path,
    env: dict[str, str] | None = None,
    on_line: Callable[[str], None] | None = None,
    timeout: int = settings.provision_timeout_seconds,
) -> SandboxResult:
    return _terraform_run(
        workspace,
        ["apply", "-input=false", "-auto-approve", "-no-color", "-var-file=terraform.tfvars"],
        env or {},
        timeout,
        on_line,
    )


def terraform_destroy(
    workspace: Path,
    env: dict[str, str] | None = None,
    on_line: Callable[[str], None] | None = None,
    timeout: int = settings.provision_timeout_seconds,
) -> SandboxResult:
    return _terraform_run(
        workspace,
        ["destroy", "-input=false", "-auto-approve", "-no-color", "-var-file=terraform.tfvars"],
        env or {},
        timeout,
        on_line,
    )


def terraform_output(workspace: Path, name: str, env: dict[str, str] | None = None) -> SandboxResult:
    return _terraform_run(
        workspace,
        ["output", "-no-color", "-json", name],
        env or {},
        timeout=120,
    )
