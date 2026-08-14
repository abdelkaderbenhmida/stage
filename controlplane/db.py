"""SQLAlchemy engine + session management.

Pool limits come from settings (§7): API and worker processes each create
engines, so unbounded pooling multiplies connections by process count and
exhausts PostgreSQL.
"""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from controlplane.core.config import settings


def _engine(url: str):
    kwargs = dict(pool_pre_ping=True, future=True)
    if url.startswith("postgres"):
        kwargs.update(
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_recycle=settings.db_pool_recycle,
        )
    return create_engine(url, **kwargs)


engine = _engine(settings.database_url)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def configure_database(url: str) -> None:
    """Point the global engine at a new URL (used by tests via testcontainers).

    Reconfigures the existing SessionLocal in place rather than rebinding the
    name — modules that did `from controlplane.db import SessionLocal` at
    import time (e.g. workers/tasks.py) hold a reference to this same
    sessionmaker object and must see the new engine too."""
    global engine
    engine = _engine(url)
    SessionLocal.configure(bind=engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
