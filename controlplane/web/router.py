"""Routes that serve the single-page web UI."""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

STATIC_DIR = Path(__file__).resolve().parent / "static"

router = APIRouter(tags=["web"], include_in_schema=False)


@router.get("/")
def index() -> FileResponse:
    """Serve the SPA shell. Client-side hash routing handles every view, so
    this is the only HTML entrypoint."""
    return FileResponse(STATIC_DIR / "index.html")


@router.get("/platform")
def platform_console() -> FileResponse:
    """Serve the platform-ops console (repo introspection + live cluster/
    CI/vault control), ported from ``ui/``. A separate page rather than a
    hash route: it authenticates against the same JWT in localStorage but
    is otherwise a standalone app."""
    return FileResponse(STATIC_DIR / "platform" / "index.html")
