"""Typed configuration for microservices using pydantic-settings.

Each service defines its own AppConfig subclass adding service-specific
secrets, then reads config once at startup via AppConfig.from_env(). Missing
required values raise SystemExit (fail-fast) instead of silently falling
back to insecure defaults — see devops-analysis-report.md P0 #3.
"""

from __future__ import annotations

import os
from typing import Optional, Type, TypeVar

T = TypeVar("T", bound="AppConfig")


class AppConfig:
    """Base config — env-driven, no plaintext defaults for secrets.

    Subclass and add fields. Required fields will raise on missing at startup.
    """

    # Common fields used by every service.
    SERVICE_NAME: str
    VAULT_ADDR: str
    VAULT_TOKEN: Optional[str] = None

    @classmethod
    def from_env(cls: Type[T]) -> T:
        """Build a config from environment variables. Raises on missing required."""
        import inspect

        instance = cls()
        annotations = getattr(cls, "__annotations__", {})
        defaults = {
            name: value
            for cls_ in reversed(inspect.getmro(cls))
            for name, value in vars(cls_).items()
            if not name.startswith("_") and not callable(value)
        }

        for field_name, field_type in annotations.items():
            env_value = os.environ.get(field_name)
            default = defaults.get(field_name, None)
            has_default = field_name in defaults

            if env_value is None or env_value == "":
                if has_default and default is not None:
                    setattr(instance, field_name, default)
                elif has_default:
                    # Optional with None default — allowed.
                    setattr(instance, field_name, None)
                else:
                    raise SystemExit(
                        f"Required environment variable '{field_name}' is not set "
                        f"(service: {cls.__name__}). Refusing to start with a "
                        f"missing required config — see devops-analysis-report.md P0 #3."
                    )
            else:
                # Coerce type where possible.
                if field_type is int:
                    setattr(instance, field_name, int(env_value))
                elif field_type is bool:
                    setattr(instance, field_name, env_value.lower() in ("1", "true", "yes"))
                else:
                    setattr(instance, field_name, env_value)

        # Validate common fields were set.
        if not getattr(instance, "SERVICE_NAME", None):
            raise SystemExit("SERVICE_NAME was not provided.")
        instance.validate()
        return instance

    def validate(self) -> None:
        """Hook for subclasses to add cross-field validation."""
        # No-op here.
        return None
