import importlib.util
import os

os.environ["ENVIRONMENT"] = "dev"
os.environ["LOG_FORMAT"] = "plain"
os.environ["VAULT_ADDR"] = ""


def _load_service(name: str):
    dirname = name
    path = os.path.join(os.path.dirname(__file__), "..", "app", dirname, "main.py")
    spec = importlib.util.spec_from_file_location(f"{name.replace('-', '_')}_main", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Trigger startup — services will generate ephemeral secrets in dev mode.
# Each test file can import these fixtures directly.


users_mod = _load_service("users-service")
products_mod = _load_service("products-service")
orders_mod = _load_service("orders-service")


def users_app():
    return users_mod.app


def products_app():
    return products_mod.app


def orders_app():
    return orders_mod.app