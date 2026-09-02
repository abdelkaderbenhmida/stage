# app/shared/

`devops-platform-shared` — the common library installed into every demo service's image
(`pip install ./shared` in `app/Dockerfile`). Provides Vault-backed secret loading,
structured JSON logging, and typed base config, so every service under `app/` fails closed
on missing secrets in the same way and emits identical log shape for aggregators (Loki,
ELK, Cloud Logging). Imported as `from shared.<module> import <name>`.

- `__init__.py` — package docstring/exports (`config`, `log_config`, `vault_client`);
  `__version__ = "1.0.0"`.
- `vault_client.py` — `get_secret()`, `vault_health()`, `reload_secrets()`,
  `SecretUnavailable`. Fail-closed Vault client: resolves a token from the Vault Agent
  Injector mount (`/vault/secrets/token`), then `VAULT_TOKEN`/`VAULT_DEV_ROOT_TOKEN_ID` env
  vars; reads secrets from `secret/data/devops-platform/<SERVICE_NAME>`; raises
  `SecretUnavailable` rather than returning an empty/fake secret on any failure.
- `log_config.py` — `setup_logging()`: idempotent root-logger setup emitting JSON
  (`python-json-logger`) by default, or a plain human-readable format when
  `LOG_FORMAT=plain` (local dev).
- `config.py` — `AppConfig` dataclass: base env-driven config (`environment`, `log_format`,
  `vault_addr`, `service_name`) that services can extend with their own fields.
- `pyproject.toml` — packaging metadata for the `devops-platform-shared` setuptools
  distribution; pins `hvac`, `prometheus-fastapi-instrumentator`, `python-json-logger`.
- `requirements.txt` — same pinned dependencies as `pyproject.toml`, used directly by
  `app/Dockerfile` before `shared` itself is installed.
- `devops_platform_shared.egg-info/` — generated setuptools build metadata (not
  hand-maintained).
