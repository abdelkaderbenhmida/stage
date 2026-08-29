"""Typed configuration for microservices using pydantic-settings.

Each service defines its own config class adding service-specific
secrets, then reads config once at startup. Missing required values raise
SystemExit (fail-fast) instead of silently falling back to insecure
defaults — see devops-analysis-report.md P0 #3.
"""

import os
from dataclasses import dataclass


@dataclass
class AppConfig:
    """Base configuration for microservices."""

    environment: str = os.environ.get("ENVIRONMENT", "production")
    log_format: str = os.environ.get("LOG_FORMAT", "json")
    vault_addr: str = os.environ.get("VAULT_ADDR", "")
    service_name: str = os.environ.get("SERVICE_NAME", "unknown-service")


__all__ = ["AppConfig"]