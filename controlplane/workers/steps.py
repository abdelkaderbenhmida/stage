"""Declared step templates for job pipeline graphs.

Must not import celery (or anything else heavy) so the API layer can import
it to build graphs for jobs whose rows have not all started yet — the
template supplies the tail of the pipeline as pending nodes.

Rows written by ``_step()`` are authoritative for label, status and timing;
the template only supplies labels for steps that have not started.
"""

from __future__ import annotations

JOB_STEP_TEMPLATES: dict[str, list[str]] = {
    "deploy": [
        "cloning repository",
        "secret scan (gitleaks) + gate",
        "dependency scan (pip-audit) + gate",
        "building image",
        "pushing image to registry",
        "trivy scan + gate",
        "rendering + applying manifests",
        "waiting for rollout",
        "capturing live URL",
    ],
    "provision:vm": [
        "terraform init",
        "terraform apply",
        "capturing node IPs",
        "ansible-playbook configure",
    ],
    "provision:namespace": [
        "rendering namespace, quota, limits and network policy",
        "applying to the shared cluster",
    ],
    "provision:pooled": [
        "claiming a pre-warmed cluster",
    ],
}
