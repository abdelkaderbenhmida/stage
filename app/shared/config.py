"""Typed configuration for microservices using pydantic-settings.

Each service defines its own config class adding service-specific
secrets, then reads config once at startup. Missing required values raise
SystemExit (fail-fast) instead of silently falling back to insecure
defaults — see devops-analysis-report.md P0 #3.
"""

__all__ = []