"""Ansible runner built on the sandbox.

The repo's ``ansible/`` directory (roles + playbook) is mounted read-only and
reused unchanged; only the rendered inventory and group_vars come from the
project workspace. The user's ephemeral SSH key is mounted read-only from a
single-run temp directory that is deleted afterwards (docs/PLATFORM_SPEC.md
§7.4).
"""

import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path

from controlplane.core.config import settings
from controlplane.runners.sandbox import SandboxResult, SandboxRun, run_sandbox

_ANSIBLE_DIR = Path(__file__).resolve().parents[2] / "ansible"

_ANSIBLE_CFG = """[defaults]
inventory = inventory.ini
roles_path = /opt/ansible/roles
host_key_checking = False
retry_files_enabled = False
pipelining = True
stdout_callback = ansible.builtin.default
default_result_format = yaml
gathering = smart
"""


def ansible_playbook(
    workspace: Path,
    ssh_private_key: str | None,
    ssh_user: str = "devops",
    on_line: Callable[[str], None] | None = None,
    timeout: int = settings.provision_timeout_seconds,
) -> SandboxResult:
    cfg = workspace / "ansible.cfg"
    cfg.write_text(_ANSIBLE_CFG)
    # Bind-mounting a path that doesn't exist yet makes Docker create it as a
    # directory, which would shadow the file the k8s_master role later tries
    # to fetch into — pre-create it empty so the mount is a plain file.
    (workspace / "kubeconfig.yaml").touch(exist_ok=True)

    env: dict[str, str] = {
        "ANSIBLE_CONFIG": str(workspace / "ansible.cfg"),
        "ANSIBLE_REMOTE_USER": ssh_user,
        "ANSIBLE_HOST_KEY_CHECKING": "False",
    }

    mounts: list[tuple[Path, str, bool]] = [
        (_ANSIBLE_DIR, "/opt/ansible", True),
    ]

    key_dir = None
    if ssh_private_key:
        key_dir = tempfile.mkdtemp(prefix="ctl-ssh-")
        key_path = Path(key_dir) / "id_rsa"
        key_path.write_text(ssh_private_key)
        key_path.chmod(0o600)
        mounts.append((key_path, "/run/ssh/id_rsa", True))
        env["ANSIBLE_PRIVATE_KEY_FILE"] = "/run/ssh/id_rsa"

    try:
        run = SandboxRun(
            command=[
                "ansible-playbook",
                "-i", str(workspace / "inventory.ini"),
                "/opt/ansible/playbook.yml",
            ],
            workspace=workspace,
            # kubeconfig.yaml: the k8s_master role fetches this project's own
            # admin credential back here (multi-tenancy Phase 3) so the
            # control plane can store it in Vault after the run.
            writable_paths=["ansible.cfg", "kubeconfig.yaml"],
            mounts=mounts,
            env=env,
            network_enabled=True,
            timeout_seconds=timeout,
            on_line=on_line,
        )
        return run_sandbox(run)
    finally:
        if key_dir:
            shutil.rmtree(key_dir, ignore_errors=True)
