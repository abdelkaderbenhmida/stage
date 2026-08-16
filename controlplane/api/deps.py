"""Shared API dependencies: DB session, authenticated user, rate limiter."""

import uuid

import jwt
from fastapi import Depends, Header, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from controlplane.core.config import settings
from controlplane.core.security import _create_token, decode_access_token
from controlplane.db import get_db
from controlplane.models import User
from controlplane.repositories.base import Scope
from controlplane.repositories.users import RefreshTokenRepository, UserRepository


def get_current_user(
    authorization: str = Header(default="", alias="Authorization"),
    db: Session = Depends(get_db),
) -> User:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    payload = decode_access_token(token)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    user = UserRepository(db).get_by_id(uuid.UUID(payload["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User inactive or missing")
    return user



def _stream_token(job_id: uuid.UUID, minutes: int = 60) -> str:
    """Generate a short-lived stream token bound to a single job.

    EventSource cannot set an Authorization header, so the log-streaming
    endpoint accepts this token as a query parameter. The token carries no
    privilege beyond streaming logs for its job_id and expires quickly so a
    token logged by a proxy is of limited value.
    """
    return _create_token(str(job_id), "stream", minutes)


def _validate_stream_token(token: str) -> uuid.UUID:
    """Validate a stream token; return the job_id it is bound to.

    Stream tokens are a distinct token type and are never accepted by
    endpoints that require an access token (decode_access_token rejects
    them), so a leaked stream token cannot be replayed against the API.
    """
    try:
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid stream token") from None
    if payload.get("type") != "stream":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")
    try:
        return uuid.UUID(payload["sub"])
    except (KeyError, ValueError, TypeError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid stream token") from None



def get_current_user_sse(
    token: str = Query(default=""),
    authorization: str = Header(default="", alias="Authorization"),
    db: Session = Depends(get_db),
) -> User:
    """Authenticate an EventSource stream.

    The browser EventSource API cannot set an Authorization header, so the
    log-streaming endpoint also accepts the access token as a query parameter.
    Note that query strings are commonly recorded in proxy and server access
    logs, so this is deliberately limited to the read-only log stream and is
    not accepted anywhere else.
    """
    raw = ""
    scheme, _, header_token = authorization.partition(" ")
    if scheme.lower() == "bearer" and header_token:
        raw = header_token
    elif token:
        raw = token

    if not raw:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    payload = decode_access_token(raw)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    user = UserRepository(db).get_by_id(uuid.UUID(payload["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User inactive or missing")
    return user



def get_refresh_tokens(request: Request) -> RefreshTokenRepository:
    return RefreshTokenRepository(request.state.db)


def get_scope(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Scope:
    """The caller's access scope: team memberships and roles in each.

    Every repository in a router must be constructed with this scope. A repo
    built with a scope that omits a team the caller belongs to silently hides
    data; one built with someone else's scope leaks it.
    """
    return Scope.from_session(db, user.id)


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def pagination_headers(request: Request, total: int, page: int, page_size: int) -> dict[str, str]:
    """RFC 8288 Link headers + X-Total-Count for list endpoints.

    Bodies stay plain lists (docs/TODO.md §8 item 6); pagination metadata
    rides in headers so existing clients are unaffected.
    """

    pages = max(1, -(-total // page_size))
    links = []
    if page > 1:
        links.append(f'<{_page_url(request, page - 1)}>; rel="prev"')
    if page < pages:
        links.append(f'<{_page_url(request, page + 1)}>; rel="next"')
    headers = {"X-Total-Count": str(total), "X-Page": str(page), "X-Page-Size": str(page_size)}
    if links:
        headers["Link"] = ", ".join(links)
    return headers


def _page_url(request: Request, page: int) -> str:
    import urllib.parse

    query = dict(request.query_params)
    query["page"] = str(page)
    return f"{request.url.path}?{urllib.parse.urlencode(query)}"


def audit(
    db: Session,
    user_id: uuid.UUID,
    action: str,
    request: Request,
    resource_type: str | None = None,
    resource_id: str | None = None,
    detail: dict | None = None,
    team_id: uuid.UUID | None = None,
) -> None:
    from controlplane.repositories.users import AuditLogRepository

    AuditLogRepository(db).record(
        user_id=user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        ip_address=client_ip(request),
        detail=detail,
        team_id=team_id,
    )
