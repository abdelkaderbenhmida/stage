"""Authentication endpoints (docs/PLATFORM_SPEC.md §8 Auth, docs/TODO.md Task 3.3 OIDC)."""

import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from controlplane.api.deps import audit, client_ip, get_current_user, get_db
from controlplane.api.rate_limit import check_rate_limit
from controlplane.api.schemas import (
    LoginRequest,
    Message,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserOut,
)
from controlplane.core import oidc
from controlplane.core.config import settings
from controlplane.core.oidc import OIDCDisabledError, OIDCError
from controlplane.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from controlplane.core.sshkeys import generate_ssh_keypair
from controlplane.core.vault import get_secret_store
from controlplane.models import User
from controlplane.repositories.teams import ensure_personal_team
from controlplane.repositories.users import RefreshTokenRepository, UserRepository

router = APIRouter(prefix="/auth", tags=["auth"])

_OIDC_FLOW_COOKIE = "oidc_flow"
_OIDC_COOKIE_TTL = getattr(settings, "oidc_flow_ttl_seconds", 600)


def _local_auth_gate() -> None:
    if not settings.local_auth_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Local password authentication is disabled. Use single sign-on.",
        )


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(body: RegisterRequest, request: Request, db: Session = Depends(get_db)) -> User:
    _local_auth_gate()
    if body.password != body.password_confirm:
        raise HTTPException(status_code=422, detail="Passwords do not match.")
    if UserRepository(db).get_by_email(body.email):
        raise HTTPException(status_code=409, detail="An account with this email already exists.")

    user = UserRepository(db).create(body.email, hash_password(body.password))
    private_pem, public_openssh = generate_ssh_keypair()
    store = get_secret_store()
    store.set(str(user.id), "ssh_private_key", private_pem)
    store.set(str(user.id), "ssh_public_key", public_openssh)
    # Every user needs a team before they can own a project; without this,
    # a freshly registered account would fail on its first create.
    ensure_personal_team(db, user)
    db.commit()
    audit(db, user.id, "user.register", request, resource_type="user", resource_id=str(user.id))
    return user


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)) -> TokenResponse:
    _local_auth_gate()
    ip = client_ip(request)
    if not check_rate_limit(f"login:{ip}", settings.login_rate_per_minute, 60):
        raise HTTPException(status_code=429, detail="Too many login attempts. Try again later.")

    user = UserRepository(db).get_by_email(body.email)
    if user is None or not verify_password(body.password, user.password_hash) or not user.is_active:
        audit(db, None, "user.login_failed", request, detail={"email": body.email})
        db.commit()
        raise HTTPException(status_code=401, detail="Invalid credentials.")

    refresh_token = generate_refresh_token()
    RefreshTokenRepository(db).store(user.id, hash_refresh_token(refresh_token), settings.refresh_token_days)
    db.commit()
    audit(db, user.id, "user.login", request)
    return TokenResponse(
        access_token=create_access_token(str(user.id), user.role),
        refresh_token=refresh_token,
    )


@router.post("/refresh", response_model=TokenResponse)
def refresh(body: RefreshRequest, request: Request, db: Session = Depends(get_db)) -> TokenResponse:
    repo = RefreshTokenRepository(db)
    stored = repo.get_active(hash_refresh_token(body.refresh_token))
    if stored is None:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token.")
    user = db.get(User, stored.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token.")

    repo.revoke(hash_refresh_token(body.refresh_token))
    new_refresh = generate_refresh_token()
    repo.store(user.id, hash_refresh_token(new_refresh), settings.refresh_token_days)
    db.commit()
    audit(db, user.id, "user.refresh", request)
    return TokenResponse(
        access_token=create_access_token(str(user.id), user.role),
        refresh_token=new_refresh,
    )


@router.post("/logout", response_model=Message)
def logout(body: RefreshRequest, request: Request, db: Session = Depends(get_db)) -> Message:
    RefreshTokenRepository(db).revoke(hash_refresh_token(body.refresh_token))
    db.commit()
    audit(db, None, "user.logout", request)
    return Message(message="Logged out.")


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> User:
    return user


@router.get("/config")
def auth_config() -> dict:
    """Tell the SPA whether SSO / local auth are available, so the login
    form can offer a "Sign in with SSO" button without hardcoding it."""
    return {
        "oidc_enabled": settings.oidc_enabled,
        "local_auth_enabled": settings.local_auth_enabled,
    }


@router.get("/oidc/login")
def oidc_login() -> RedirectResponse:
    """Start the SSO flow: browser redirects to the provider.

    The ``state``/``nonce``/``code_verifier`` trip is kept in a signed,
    short-lived, HttpOnly cookie — nothing is stored server-side, so the
    flow survives any number of API workers, and the cookie cannot be forged
    without the JWT secret.
    """
    try:
        authorize_url, state, nonce, code_verifier = oidc.build_authorize_url()
    except OIDCDisabledError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except OIDCError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    response = RedirectResponse(authorize_url, status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        _OIDC_FLOW_COOKIE,
        oidc.encode_flow_cookie(state, nonce, code_verifier),
        max_age=_OIDC_COOKIE_TTL,
        httponly=True,
        samesite="lax",
        secure=not settings.is_dev,
    )
    return response


def _flow_cookie(request: Request) -> dict:
    raw = request.cookies.get(_OIDC_FLOW_COOKIE)
    if raw is None:
        raise HTTPException(status_code=400, detail="Missing OIDC flow cookie. Start the flow again.")
    flow = oidc.decode_flow_cookie(raw)
    if flow is None:
        raise HTTPException(status_code=400, detail="Invalid or expired OIDC flow. Start the flow again.")
    return flow


@router.get("/oidc/callback")
def oidc_callback(
    request: Request,
    db: Session = Depends(get_db),
    code: str = Query(default=""),
    state: str = Query(default=""),
) -> RedirectResponse:
    """The provider redirects here with the authorization code.

    Verifies the signed flow cookie and the ``state`` (CSRF), exchanges the
    code with PKCE, verifies the ID token signature against the JWKS plus
    its iss/aud/exp/nonce claims, then provisions the user. SSO accounts
    cannot log in with a password — their password hash is a random string.
    """
    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code.")
    flow = _flow_cookie(request)
    if not state or state != flow.get("state"):
        raise HTTPException(status_code=400, detail="State mismatch. Start the flow again.")

    try:
        token = oidc.exchange_code(code, flow["verifier"])
        claims = oidc.verify_id_token(token["id_token"], flow["nonce"])
    except OIDCDisabledError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except OIDCError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    email = claims.get("email")
    if not email:
        raise HTTPException(status_code=401, detail="Identity provider did not return an email claim.")

    groups = claims.get(settings.oidc_group_claim) or []
    if isinstance(groups, str):
        groups = [groups]

    user, first_login = oidc.provision_sso_user(db, email, groups)
    tokens = oidc.issue_tokens(db, user)
    audit(
        db,
        user.id,
        "user.sso_login",
        request,
        resource_type="user",
        resource_id=str(user.id),
        detail={"first_login": first_login},
    )

    # SPA popup flow: hand the tokens to the opener via postMessage, then
    # close. The tokens never appear in the URL bar or server logs.
    payload = {"source": "controlplane-oidc", "email": user.email, **tokens}
    script = json.dumps(payload)
    html = f"""<!doctype html>
<html><head><title>Signed in</title></head>
<body>
<script>
window.opener.postMessage({script}, window.location.origin);
window.close();
</script>
</body></html>"""
    return HTMLResponse(html)
