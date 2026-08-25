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
    ``#/platform/*`` — so this is the only HTML entrypoint.

    ``no-cache`` means revalidate, not "never store": the browser still gets a
    304 for an unchanged shell. It is here because the response headers travel
    with this document — the CSP that decides whether the operator console may
    embed a dashboard is one of them. Served heuristically cacheable, an open
    browser kept enforcing a CSP from a previous deploy and every "open
    dashboard" button stayed a blank frame long after the header was fixed,
    with the fix invisible until a manual hard reload.
    """
    return FileResponse(
        STATIC_DIR / "index.html",
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )
