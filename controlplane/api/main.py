"""Control-plane API entrypoint (docs/PLATFORM_SPEC.md §4, §8)."""

import logging
import time
import uuid
from contextlib import asynccontextmanager

import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from controlplane.api.routers import (
    auth,
    catalogue,
    deployments,
    infrastructure,
    jobs,
    logs,
    monitoring,
    platform,
    projects,
    scans,
    teams,
    webhooks,
)
from controlplane.core.config import settings
from controlplane.core.logging import log_extra, request_id_var, setup_logging
from controlplane.web import router as web_router
from controlplane.web.router import STATIC_DIR

setup_logging(settings.log_format)

logger = logging.getLogger("controlplane.api")

APP_VERSION = "0.1.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.require_jwt_secret()
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="DevOps Central Platform — Control Plane",
        version=APP_VERSION,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.is_dev else [],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_id_and_access_log(request, call_next):
        """Assign/echo an X-Request-Id and emit an access log line.

        The id is propagated into logs via the ``request_id_var`` context
        variable, so a request can be correlated with the Celery tasks it
        queued (docs/TODO.md §7).
        """
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        token = request_id_var.set(request_id)
        started = time.monotonic()
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers.setdefault("X-Request-Id", request_id)
        duration_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            f"{request.method} {request.url.path} -> {response.status_code} ({duration_ms}ms)",
            extra=log_extra(
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                duration_ms=duration_ms,
            ),
        )
        return response

    @app.middleware("http")
    async def security_headers(request, call_next):
        """Baseline hardening for the UI, which is same-origin with the API.

        script-src is 'self' with no exception anywhere: the console is
        buildless and every button now dispatches through a `data-act`
        attribute table, so there is no inline handler left for a CSP to
        have to whitelist.

        style-src keeps 'unsafe-inline' because both halves of the console
        build markup as template literals carrying `style="..."` attributes
        in a few hundred places. That is a formatting concern, not a script
        execution one.
        """
        response = await call_next(request)
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
            "base-uri 'self'; form-action 'self'",
        )
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if not settings.is_dev:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    def readyz():
        try:
            from controlplane.db import SessionLocal

            with SessionLocal() as session:
                session.execute(sa.text("SELECT 1"))
            db_ok = True
        except Exception:
            db_ok = False
        return {"status": "ready" if db_ok else "degraded", "database": db_ok}

    # Control-plane metrics are exposed in every environment: the platform is
    # itself a production service, and its own SLOs (job latency, failure
    # rate) are what AlertManager watches (PLATFORM_SPEC §9, OPERATIONS.md §5).
    try:
        from prometheus_fastapi_instrumentator import Instrumentator

        Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
        from controlplane.api.metrics import register_health_metrics

        register_health_metrics()
    except ImportError:
        pass

    for router in (
        auth.router,
        teams.router,
        projects.router,
        infrastructure.router,
        deployments.router,
        scans.router,
        jobs.router,
        catalogue.router,
        webhooks.router,
        logs.router,
        monitoring.router,
        platform.router,
        # Separate router: same platform-admin rule, but resolves the caller
        # from a query-parameter token because EventSource cannot send a
        # header. Must be registered alongside, not inside, platform.router.
        platform.stream_router,
    ):
        app.include_router(router, prefix="/api/v1")

    # Web UI last so its catch-all "/" never shadows an API route.
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(web_router)

    return app


app = create_app()
