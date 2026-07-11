"""DevOps Central Platform shared library.

Import from services as:
    from shared.vault_client import get_secret, vault_health
    from shared.logging import setup_logging
    from shared.config import AppConfig
"""

__version__ = "1.0.0"
__all__ = ["vault_client", "config", "logging"]
