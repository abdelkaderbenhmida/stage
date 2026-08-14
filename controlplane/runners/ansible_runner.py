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
stdout_callback = yaml
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
        key_path = Path(key_dir) / "id_ed25519"
        key_path.write_text(ssh_private_key)
        key_path.chmod(0o600)
        mounts.append((key_path, "/run/ssh/id_ed25519", True))
        env["ANSIBLE_PRIVATE_KEY_FILE"] = "/run/ssh/id_ed25519"

    try:
        run = SandboxRun(
            command=[
                "ansible-playbook",
                "-i", str(workspace / "inventory.ini"),
                "/opt/ansible/playbook.yml",
            ],
            workspace=workspace,
            writable_paths=["ansible.cfg"],
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
