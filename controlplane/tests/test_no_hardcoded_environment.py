"""Environment-specific values come from settings, never from literals.

Every value in here is one that changes when this platform is deployed
somewhere else: the cluster's DNS suffix, where metrics/logs live, the base
image an auto-built tenant app is compiled from. Baking any of them into the
code means the only way to move environments is a source edit, and — for the
auto-build base image — that a newly-disclosed CVE silently blocks every
tenant deployment until someone can cut a release.

These tests assert the wiring, not the defaults: overriding the environment
variable has to actually change what the code produces.

Deliberately no ``importlib.reload``: reloading the config (or the worker)
module swaps the process-wide ``settings`` object and re-runs the worker's
import side effects, which corrupts every later test in the session. A fresh
``Settings()`` reads the patched environment through its own default
factories, and the render path is exercised by patching the live object.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from controlplane.core.config import Settings

CONTROLPLANE = Path(__file__).resolve().parent.parent


def test_cluster_domain_is_configurable(monkeypatch):
    monkeypatch.setenv("CLUSTER_DOMAIN", "cluster.internal")
    assert Settings().cluster_domain == "cluster.internal"


def test_autobuild_settings_are_configurable(monkeypatch):
    monkeypatch.setenv("AUTOBUILD_BASE_IMAGE", "python:3.13-slim")
    monkeypatch.setenv("AUTOBUILD_RUN_UID", "4242")
    monkeypatch.setenv("AUTOBUILD_SERVER_PACKAGE", "granian>=1.0")
    monkeypatch.setenv("AUTOBUILD_PIP_HARDENING", "setuptools>=99")

    settings = Settings()
    assert settings.autobuild_base_image == "python:3.13-slim"
    assert settings.autobuild_run_uid == 4242
    assert settings.autobuild_server_package == "granian>=1.0"
    assert settings.autobuild_pip_hardening == "setuptools>=99"


def test_observability_backends_are_configurable(monkeypatch):
    monkeypatch.setenv("PROMETHEUS_URL", "http://prom.example:9090")
    monkeypatch.setenv("LOKI_URL", "http://loki.example:3100")
    monkeypatch.setenv("ELASTICSEARCH_URL", "http://es.example:9200")

    settings = Settings()
    assert settings.prometheus_url == "http://prom.example:9090"
    assert settings.loki_url == "http://loki.example:3100"
    assert settings.elasticsearch_url == "http://es.example:9200"


def test_generated_dockerfile_follows_the_configured_base_image(tmp_path, monkeypatch):
    """The point of the settings: the rendered Dockerfile must actually use
    them, not just carry them."""
    from controlplane.workers import tasks

    monkeypatch.setenv("AUTOBUILD_BASE_IMAGE", "python:3.13-slim")
    monkeypatch.setenv("AUTOBUILD_RUN_UID", "4242")
    monkeypatch.setenv("AUTOBUILD_SERVER_PACKAGE", "granian>=1.0")
    # Settings is frozen, so swap the whole object rather than its fields.
    monkeypatch.setattr(tasks, "settings", Settings())

    (tmp_path / "requirements.txt").write_text("fastapi>=0.121\n")
    (tmp_path / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n")

    assert tasks._try_autogenerate_dockerfile(tmp_path, 8080) == "main"
    rendered = (tmp_path / "Dockerfile").read_text()

    assert "FROM python:3.13-slim" in rendered
    assert "--uid 4242" in rendered
    assert "granian>=1.0" in rendered
    assert "python:3.11-slim" not in rendered


def test_cluster_domain_reaches_the_rendered_url(monkeypatch):
    from controlplane.workers import tasks

    monkeypatch.setenv("CLUSTER_DOMAIN", "cluster.internal")
    monkeypatch.setattr(tasks, "settings", Settings())
    assert tasks._cluster_domain() == "cluster.internal"


@pytest.mark.parametrize(
    ("relative_path", "literal"),
    [
        ("workers/tasks.py", "devops.local"),
        ("renderers/namespace.py", "elasticsearch.monitoring.svc"),
    ],
)
def test_no_environment_literals_left_outside_settings(relative_path, literal):
    """These names belong to one specific deployment of this platform; they
    must reach the code through settings so another deployment can differ."""
    path = CONTROLPLANE / relative_path
    offenders = [
        f"{path.name}:{number}: {stripped}"
        for number, line in enumerate(path.read_text().splitlines(), 1)
        if (stripped := line.strip())
        and not stripped.startswith("#")
        and "_env(" not in stripped  # the setting's own default
        and literal in stripped
    ]
    assert not offenders, "environment-specific literals outside settings:\n" + "\n".join(offenders)
