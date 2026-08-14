"""OIDC single sign-on (docs/TODO.md Task 3.3).

Authorization-code flow with PKCE, implemented with authlib for the OAuth
dance and PyJWT for ID-token verification against the provider's JWKS.

Security properties, each enforced in this module:

- The OAuth ``state`` is bound to a server-issued signed cookie, so a
  forged callback cannot swap in a victim's authorization code.
- The ID token signature is verified against the provider's JWKS, and its
  ``iss``, ``aud``, ``exp`` and ``nonce`` claims are checked. Skipping any
  of these turns SSO into an authentication bypass.
- The code exchange uses PKCE (S256), so a leaked authorization code cannot
  be redeemed without the ``code_verifier`` that only the original browser
  session knows.
- The group claim maps to platform roles through a configurable mapping
  (``OIDC_ROLE_MAP``); unknown users fall back to the default role.

The flow is stateless on the server: ``state``/``nonce``/``code_verifier``
live in a short-lived signed cookie, so nothing must be stored in Redis or
a session table.
"""

import secrets
from datetime import UTC, datetime, timedelta

import httpx
import jwt
from authlib.integrations.httpx_client import OAuth2Client
from sqlalchemy.orm import Session

from controlplane.core.config import settings
from controlplane.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    random_hex,
)
from controlplane.core.sshkeys import generate_ssh_keypair
from controlplane.core.vault import get_secret_store
from controlplane.models import User
from controlplane.repositories.teams import ensure_personal_team
from controlplane.repositories.users import RefreshTokenRepository, UserRepository

_DISCOVERY_PATH = "/.well-known/openid-configuration"
_HTTP_TIMEOUT = httpx.Timeout(15.0)

_discovery_cache: dict[str, dict] = {}
_discovery_cache_ts: dict[str, float] = {}
# Test seam: an httpx transport used for every outbound request when the
# caller does not pass one explicitly. Production never sets this.
_transport: httpx.BaseTransport | None = None


def set_transport(transport: httpx.BaseTransport | None) -> None:
    """Override the HTTP transport for all OIDC calls (tests only)."""
    global _transport
    _transport = transport


def _resolve_transport(transport: httpx.BaseTransport | None) -> httpx.BaseTransport | None:
    return transport if transport is not None else _transport


class OIDCError(Exception):
    """Raised for any failed step of the SSO flow; the router maps it to a
    400/401 response."""


class OIDCDisabledError(OIDCError):
    """SSO is not configured; callers answer 404."""


def _require_enabled() -> None:
    if not settings.oidc_enabled:
        raise OIDCDisabledError("OIDC is not enabled.")


def require_oidc_config() -> None:
    """Fail fast when SSO is enabled but misconfigured."""
    _require_enabled()
    missing = [
        name
        for name in ("oidc_issuer", "oidc_client_id", "oidc_client_secret", "oidc_redirect_uri")
        if not getattr(settings, name)
    ]
    if missing:
        raise OIDCError(f"OIDC enabled but missing configuration: {', '.join(missing)}.")


def discover(issuer: str, transport: httpx.BaseTransport | None = None) -> dict:
    """Fetch the provider's OpenID configuration, cached for 10 minutes.

    ``transport`` exists for tests: an httpx.MockTransport can serve a fake
    provider without any network.
    """
    now = datetime.now(UTC).timestamp()
    cached = _discovery_cache.get(issuer)
    if cached and now - _discovery_cache_ts.get(issuer, 0) < 600:
        return cached
    url = issuer.rstrip("/") + _DISCOVERY_PATH
    with httpx.Client(transport=_resolve_transport(transport), timeout=_HTTP_TIMEOUT) as client:
        resp = client.get(url)
    if resp.status_code != 200:
        raise OIDCError(f"Failed to fetch OIDC discovery document: HTTP {resp.status_code}.")
    data = resp.json()
    for key in ("issuer", "authorization_endpoint", "token_endpoint", "jwks_uri"):
        if not data.get(key):
            raise OIDCError(f"Discovery document missing {key!r}.")
    _discovery_cache[issuer] = data
    _discovery_cache_ts[issuer] = now
    return data


def clear_discovery_cache() -> None:
    _discovery_cache.clear()
    _discovery_cache_ts.clear()


def encode_flow_cookie(state: str, nonce: str, code_verifier: str) -> str:
    """Sign the flow parameters into a short-lived cookie value."""
    return jwt.encode(
        {
            "type": "oidc_flow",
            "state": state,
            "nonce": nonce,
            "verifier": code_verifier,
            "iat": datetime.now(UTC),
            "exp": datetime.now(UTC) + timedelta(seconds=settings.oidc_flow_ttl_seconds),
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


def decode_flow_cookie(token: str) -> dict | None:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError:
        return None
    if payload.get("type") != "oidc_flow":
        return None
    return payload


def build_authorize_url(transport: httpx.BaseTransport | None = None) -> tuple[str, str, str, str]:
    """Build the provider's authorize URL (PKCE S256).

    Returns ``(authorize_url, state, nonce, code_verifier)``. The caller
    stores ``state``/``nonce``/``code_verifier`` in a signed cookie.
    """
    _require_enabled()
    require_oidc_config()
    meta = discover(settings.oidc_issuer, transport)
    state = random_hex(24)
    nonce = random_hex(24)
    code_verifier = secrets.token_urlsafe(48)
    client = OAuth2Client(
        client_id=settings.oidc_client_id,
        client_secret=settings.oidc_client_secret,
        redirect_uri=settings.oidc_redirect_uri,
        code_challenge_method="S256",
        transport=_resolve_transport(transport),
        timeout=_HTTP_TIMEOUT,
    )
    url, _ = client.create_authorization_url(
        meta["authorization_endpoint"],
        state=state,
        code_verifier=code_verifier,
        scope=settings.oidc_scope,
        nonce=nonce,
    )
    return url, state, nonce, code_verifier


def exchange_code(
    code: str,
    code_verifier: str,
    transport: httpx.BaseTransport | None = None,
) -> dict:
    """Exchange the authorization code for tokens (PKCE)."""
    meta = discover(settings.oidc_issuer, transport)
    client = OAuth2Client(
        client_id=settings.oidc_client_id,
        client_secret=settings.oidc_client_secret,
        redirect_uri=settings.oidc_redirect_uri,
        transport=_resolve_transport(transport),
        timeout=_HTTP_TIMEOUT,
    )
    try:
        token = client.fetch_token(
            meta["token_endpoint"],
            code=code,
            code_verifier=code_verifier,
            grant_type="authorization_code",
        )
    except Exception as exc:  # noqa: BLE001 - authlib raises various OAuth2Error subclasses
        raise OIDCError(f"Token exchange failed: {exc}") from exc
    if not token.get("id_token"):
        raise OIDCError("Provider returned no id_token.")
    return token


def _jwk_keys(jwks: dict) -> list:
    keys = []
    for jwk_data in jwks.get("keys", []):
        kty = jwk_data.get("kty")
        if kty == "RSA":
            keys.append(jwt.algorithms.RSAAlgorithm.from_jwk(jwk_data))
        elif kty == "EC":
            keys.append(jwt.algorithms.ECAlgorithm.from_jwk(jwk_data))
    return keys


def verify_id_token(
    id_token: str,
    expected_nonce: str,
    transport: httpx.BaseTransport | None = None,
) -> dict:
    """Verify the ID token signature against the provider JWKS and its
    iss/aud/exp/nonce claims. Returns the validated claims.
    """
    meta = discover(settings.oidc_issuer, transport)
    with httpx.Client(transport=_resolve_transport(transport), timeout=_HTTP_TIMEOUT) as client:
        resp = client.get(meta["jwks_uri"])
    if resp.status_code != 200:
        raise OIDCError(f"Failed to fetch JWKS: HTTP {resp.status_code}.")
    unverified = jwt.get_unverified_header(id_token)
    alg = unverified.get("alg")
    keys = _jwk_keys(resp.json())
    if not keys:
        raise OIDCError("No usable keys in provider JWKS.")

    last_error: Exception | None = None
    for key in keys:
        try:
            claims = jwt.decode(
                id_token,
                key,
                algorithms=[alg] if alg else None,
                audience=settings.oidc_client_id,
                issuer=meta["issuer"],
                options={"require": ["exp", "iss", "aud", "nonce"]},
            )
            if claims.get("nonce") != expected_nonce:
                raise OIDCError("ID token nonce mismatch.")
            return claims
        except jwt.PyJWTError as exc:
            last_error = exc
            continue
    raise OIDCError(f"ID token verification failed: {last_error}")


def role_from_groups(groups: list[str]) -> str:
    """Map IdP groups to a platform role per OIDC_ROLE_MAP."""
    mapping = settings.oidc_role_map or {}
    for group in groups:
        roles = mapping.get(group)
        if roles:
            return roles[0]
    return "user"


def provision_sso_user(
    db: Session,
    email: str,
    groups: list[str],
) -> tuple[User, bool]:
    """Create or update the user behind an SSO login.

    First login: creates the user, their personal team and an SSH keypair.
    Later logins refresh the role from the current group membership.
    Returns ``(user, created)``.
    """
    repo = UserRepository(db)
    user = repo.get_by_email(email)
    created = user is None
    if user is None:
        # No usable password: an SSO account must not be able to log in with
        # a guessed password once local auth is disabled in production.
        unusable_hash = hash_password(secrets.token_urlsafe(32))
        user = repo.create(email, unusable_hash, role=role_from_groups(groups))
        store = get_secret_store()
        private_pem, public_openssh = generate_ssh_keypair()
        store.set(str(user.id), "ssh_private_key", private_pem)
        store.set(str(user.id), "ssh_public_key", public_openssh)
        ensure_personal_team(db, user)
    else:
        new_role = role_from_groups(groups)
        if user.role != new_role:
            user.role = new_role
    db.commit()
    return user, created


def issue_tokens(db: Session, user: User) -> dict:
    """Issue access/refresh tokens exactly like local /auth/login does."""
    refresh_token = generate_refresh_token()
    RefreshTokenRepository(db).store(
        user.id, hash_refresh_token(refresh_token), settings.refresh_token_days
    )
    db.commit()
    return {
        "access_token": create_access_token(str(user.id), user.role),
        "refresh_token": refresh_token,
    }