"""Self-Service DevOps Platform — control plane.

Implements docs/PLATFORM_SPEC.md: a FastAPI + Celery control plane that
renders and drives the existing Terraform/Ansible/Kubernetes assets so users
can provision infrastructure and deploy services through a web UI.
"""

__version__ = "0.1.0"
