"""Routes that serve the single-page web UI."""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

STATIC_DIR = Path(__file__).resolve().parent / "static"

router = APIRouter(tags=["web"], include_in_schema=False)


@router.get("/")
def index() -> FileResponse:
    """Serve the SPA shell. Client-side hash routing handles every view —
    the control-plane ones and the platform-ops console under
    ``#/platform/*`` — so this is the only HTML entrypoint."""
    return FileResponse(STATIC_DIR / "index.html")
