"""Secrets must not reach the sandbox through the command line.

`docker run -e TOKEN=...` puts the value in the argv of a host process, and
argv is world-readable through /proc. Any local user could read a tenant's
registry password or git token for as long as the container ran, and it would
also be captured by anything that records process invocations.

`secret_env` exists to keep those values out of argv entirely.
"""

import os
import subprocess

from controlplane.runners import sandbox
from controlplane.runners.sandbox import SandboxRun, run_sandbox

SECRET = "s3cr3t-do-not-leak-abcdef"


def _captured_argv(monkeypatch) -> list[str]:
    seen: dict[str, list[str]] = {}
    real_popen = subprocess.Popen

    class _Fake:
        def __init__(self, args, **kwargs):
            seen["args"] = list(args)
            self._proc = real_popen(["true"], **kwargs)
            self.stdout = self._proc.stdout
            self.returncode = 0

        def poll(self):
            return self._proc.poll()

        def wait(self, timeout=None):
            return self._proc.wait(timeout=timeout)

        def kill(self):
            self._proc.kill()

    monkeypatch.setattr(sandbox.subprocess, "Popen", _Fake)
    monkeypatch.setattr(sandbox.subprocess, "run", lambda *a, **k: None)
    return seen


def test_secret_env_never_appears_in_the_command_line(monkeypatch):
    seen = _captured_argv(monkeypatch)
    run_sandbox(SandboxRun(command=["true"], secret_env={"TOKEN": SECRET}, network_enabled=False))

    argv = " ".join(seen["args"])
    assert SECRET not in argv, "secret leaked into docker run argv"
    assert "--env-file" in seen["args"], "secret must travel via --env-file"


def test_plain_env_still_uses_flags(monkeypatch):
    """Non-secret values stay on the command line; only secrets are special."""
    seen = _captured_argv(monkeypatch)
    run_sandbox(SandboxRun(command=["true"], env={"KUBECONFIG": "/kube/config"}, network_enabled=False))
    assert "KUBECONFIG=/kube/config" in seen["args"]


def test_secret_file_is_private_and_removed(monkeypatch):
    """The file holding the secret must not be world-readable, nor outlive the run."""
    modes: list[int] = []
    paths: list[str] = []
    # Called for the Popen patch it installs; this test asserts on the file's
    # mode and lifetime, not on the argv it captures.
    _captured_argv(monkeypatch)

    real_chmod = os.chmod

    def _spy_chmod(path, mode, *a, **k):
        modes.append(mode)
        paths.append(str(path))
        return real_chmod(path, mode, *a, **k)

    monkeypatch.setattr(sandbox.Path, "chmod", lambda self, mode: (modes.append(mode), paths.append(str(self)), real_chmod(self, mode))[-1])

    run_sandbox(SandboxRun(command=["true"], secret_env={"TOKEN": SECRET}, network_enabled=False))

    assert modes and modes[0] == 0o600, f"secret file mode was {modes}"
    assert paths and not os.path.exists(paths[0]), "secret file outlived the run"


def test_newlines_cannot_forge_extra_variables(monkeypatch):
    """--env-file parses one KEY=VALUE per line and does no quoting.

    A newline inside a secret would therefore define a second, attacker-chosen
    variable in the sandbox.
    """
    contents: list[str] = []
    real_chmod = os.chmod

    def _capture(self, mode):
        contents.append(self.read_text())
        return real_chmod(self, mode)

    monkeypatch.setattr(sandbox.Path, "chmod", _capture)
    _captured_argv(monkeypatch)

    run_sandbox(
        SandboxRun(
            command=["true"],
            secret_env={"TOKEN": "abc\nFORGED=pwned"},
            network_enabled=False,
        )
    )

    assert contents, "secret file was never written"
    written = contents[0]
    assert "FORGED=pwned" not in written.split("\n")[1:], "newline forged a second variable"
    assert written.count("\n") == 1, f"expected exactly one variable, got: {written!r}"
