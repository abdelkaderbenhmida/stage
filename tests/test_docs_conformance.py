"""The README must keep describing the system that exists.

Documentation goes stale silently: nothing fails, and the next person reads a
table of database tables that is missing one, or a list of background jobs that
never mentions the task holding their warm pool at size. By the time anyone
notices, the document has stopped being a reference and become folklore.

These are the claims that can be checked mechanically — inventories the code
already knows the true answer to. Prose cannot be tested and is not tested
here; the point is that the lists stay honest, so the prose around them is
worth trusting.

When one of these fails, the fix is to update the README, not the test.
"""

import os
import re
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO_ROOT)

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("JWT_SECRET", "test-secret-not-used-for-signing-here")
os.environ.setdefault("ENVIRONMENT", "dev")
os.environ.setdefault("LOG_FORMAT", "plain")
os.environ.setdefault("VAULT_ADDR", "")

def _read(*parts: str) -> str:
    with open(os.path.join(REPO_ROOT, *parts), encoding="utf-8") as handle:
        return handle.read()


README = _read("README.md")

_HTTP_METHODS = ("get", "post", "put", "patch", "delete")


def test_every_database_table_is_documented():
    """A table absent from the data model section is a part of the system a
    reader has no way to learn about."""
    from controlplane.models import Base

    missing = sorted(t for t in Base.metadata.tables if f"`{t}`" not in README)

    assert not missing, f"tables missing from README's data model section: {missing}"


def test_every_background_task_is_documented():
    tasks_py = _read("controlplane", "workers", "tasks.py")
    declared = set(re.findall(r'name="controlplane\.workers\.tasks\.([a-z_]+)"', tasks_py))
    missing = sorted(t for t in declared if f"`{t}`" not in README)

    assert declared, "no celery tasks found — the regex above has drifted from tasks.py"
    assert not missing, f"celery tasks missing from README's background jobs section: {missing}"


def test_every_script_is_documented():
    """`scripts/` is what an operator reaches for. One that nothing mentions
    is one nobody runs."""
    scripts = {
        f
        for f in os.listdir(os.path.join(REPO_ROOT, "scripts"))
        if f.endswith((".sh", ".py"))
    }
    missing = sorted(f for f in scripts if f not in README)

    assert scripts, "no scripts found — has the directory moved?"
    assert not missing, f"scripts missing from README's scripts table: {missing}"


def test_documented_endpoint_count_matches_the_api():
    """The README states a number. A number is either right or misleading, and
    this one had drifted by seven."""
    from controlplane.api.main import create_app

    paths = create_app().openapi()["paths"]
    actual = sum(
        1
        for path, operations in paths.items()
        if path.startswith("/api/v1")
        for method in operations
        if method in _HTTP_METHODS
    )

    stated = re.search(r"under `/api/v1`\. (\d+) endpoints", README)
    assert stated, "README no longer states an endpoint count in the expected form"
    assert int(stated.group(1)) == actual, (
        f"README says {stated.group(1)} endpoints, the app serves {actual}"
    )


def test_documented_platform_endpoint_count_matches_the_api():
    from controlplane.api.main import create_app

    paths = create_app().openapi()["paths"]
    actual = sum(
        1
        for path, operations in paths.items()
        if path.startswith("/api/v1") and "/platform" in path
        for method in operations
        if method in _HTTP_METHODS
    )

    stated = re.search(r"\*\*platform\*\* \(admin\) \| (\d+) endpoints", README)
    assert stated, "README no longer states a platform endpoint count in the expected form"
    assert int(stated.group(1)) == actual, (
        f"README says {stated.group(1)} platform endpoints, the app serves {actual}"
    )
