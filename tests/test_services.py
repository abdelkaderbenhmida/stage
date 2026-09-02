import importlib
import os
import sys
from fastapi.testclient import TestClient

os.environ["ENVIRONMENT"] = "dev"
os.environ["LOG_FORMAT"] = "plain"
os.environ["VAULT_ADDR"] = ""


def test_shared_imports():
    from shared.config import AppConfig
    from shared.log_config import setup_logging
    from shared.vault_client import _is_vault_configured

    cfg = AppConfig()
    assert cfg.environment == "dev"
    logger = setup_logging("test-service")
    assert logger is not None
    assert _is_vault_configured() is False


def test_users_service():
    sys.path.insert(0, os.path.abspath("app/users-service"))
    sys.path.insert(0, os.path.abspath("app/shared"))
    import main as users_main
    importlib.reload(users_main)

    client = TestClient(users_main.app)
    res_root = client.get("/")
    assert res_root.status_code == 200
    assert res_root.json()["service"] == "users"

    res_live = client.get("/livez")
    assert res_live.status_code == 200
    assert res_live.json()["status"] == "alive"

    res_users = client.get("/users")
    assert res_users.status_code == 200
    assert len(res_users.json()) >= 2
