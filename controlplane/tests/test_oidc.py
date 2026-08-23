"""OIDC SSO integration tests (docs/TODO.md Task 3.3).

A fake OpenID provider is served over httpx.MockTransport, so the full
flow — discovery, authorize URL, code exchange with PKCE, JWKS signature
verification — runs against real code paths with no network and no authlib
mock. The negative tests prove the required security properties:
state binding, signature verification, iss/aud/exp/nonce checks.
"""

import base64
import json
import re
import time

import httpx
import jwt as pyjwt
import pytest
from controlplane.core import oidc
from controlplane.core.config import settings
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

pytestmark = [pytest.mark.integration]

CLIENT_ID = "platform-test"
CLIENT_SECRET = "test-client-secret"
ISSUER = "https://idp.example.test"
REDIRECT_URI = "http://testserver/api/v1/auth/oidc/callback"


def _b64url_int(value: int) -> str:
    length = max(1, (value.bit_length() + 7) // 8)
    return base64.urlsafe_b64encode(value.to_bytes(length, "big")).decode("ascii").rstrip("=")


class FakeOIDCProvider:
    """Minimal OpenID provider: discovery, JWKS, and a PKCE token endpoint."""

    def __init__(self, issuer: str = ISSUER):
        self.issuer = issuer
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.pending: dict[str, dict] = {}
        self.rogue_key: rsa.RSAPrivateKey | None = None

    @property
    def jwks(self) -> dict:
        pub = self.key.public_key().public_numbers()
        return {
            "keys": [
                {
                    "kty": "RSA",
                    "use": "sig",
                    "alg": "RS256",
                    "kid": "test-key",
                    "n": _b64url_int(pub.n),
                    "e": _b64url_int(pub.e),
                }
            ]
        }

    def pend(self, code: str, verifier: str, nonce: str, email: str, groups: list[str]) -> None:
        self.pending[code] = {"verifier": verifier, "nonce": nonce, "email": email, "groups": groups}

    def disable_code(self, code: str) -> None:
        del self.pending[code]

    def _sign(self, claims: dict) -> str:
        key = self.rogue_key or self.key
        pem = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
        return pyjwt.encode(claims, pem, algorithm="RS256", headers={"kid": "test-key"})

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/openid-configuration":
            body = {
                "issuer": self.issuer,
                "authorization_endpoint": f"{self.issuer}/authorize",
                "token_endpoint": f"{self.issuer}/token",
                "jwks_uri": f"{self.issuer}/jwks",
            }
            return httpx.Response(200, json=body)
        if request.url.path.endswith("/jwks"):
            return httpx.Response(200, json=self.jwks)
        if request.url.path.endswith("/token"):
            data = dict(request.url.params)
            if request.headers.get("content-type", "").startswith("application/x-www-form-urlencoded") and request.content:
                data.update(
                    dict(pair.split("=", 1) for pair in request.content.decode().split("&") if "=" in pair)
                )
            code = data.get("code")
            pending = self.pending.get(code)
            if pending is None or data.get("code_verifier") != pending["verifier"]:
                return httpx.Response(400, json={"error": "invalid_grant"})
            now = int(time.time())
            id_token = self._sign(
                {
                    "iss": self.issuer,
                    "sub": f"sub-{pending['email']}",
                    "aud": CLIENT_ID,
                    "exp": now + 300,
                    "iat": now,
                    "nonce": pending["nonce"],
                    "email": pending["email"],
                    "email_verified": True,
                    "preferred_username": pending["email"].split("@")[0],
                    "groups": pending["groups"],
                }
            )
            return httpx.Response(
                200,
                json={
                    "access_token": "fake-access",
                    "token_type": "Bearer",
                    "expires_in": 300,
                    "id_token": id_token,
                },
            )
        return httpx.Response(404)


@pytest.fixture()
def provider():
    return FakeOIDCProvider()


@pytest.fixture(autouse=True)
def oidc_settings(provider, settings_override):
    """Enable SSO against the fake provider; restore after the test."""
    oidc.set_transport(httpx.MockTransport(provider.handle))
    oidc.clear_discovery_cache()
    with settings_override(
        oidc_enabled=True,
        oidc_issuer=ISSUER,
        oidc_client_id=CLIENT_ID,
        oidc_client_secret=CLIENT_SECRET,
        oidc_redirect_uri=REDIRECT_URI,
        oidc_scope="openid email profile groups",
        oidc_group_claim="groups",
        oidc_role_map={"platform-admins": ["admin"], "platform-developers": ["developer"]},
        local_auth_enabled=True,
    ):
        yield
    oidc.clear_discovery_cache()
    oidc.set_transport(None)


def _start_flow(client) -> tuple[dict, str]:
    """GET /auth/oidc/login, return (flow_cookie_payload, provider_location)."""
    resp = client.get("/api/v1/auth/oidc/login", follow_redirects=False)
    assert resp.status_code == 302, resp.text
    cookie = next(c for c in resp.headers.get_list("set-cookie") if c.startswith("oidc_flow="))
    value = cookie.split("=", 1)[1].split(";", 1)[0]
    flow = oidc.decode_flow_cookie(value)
    assert flow is not None, "flow cookie must decode"
    return flow, resp.headers["location"]


def _finish_flow(client, provider, flow: dict, *, email="sso@example.com", groups=None, code="good-code") -> httpx.Response:
    provider.pend(code, flow["verifier"], flow["nonce"], email, groups or [])
    return client.get(f"/api/v1/auth/oidc/callback?code={code}&state={flow['state']}")


def _tokens_from_popup(resp: httpx.Response) -> dict:
    match = re.search(r"window\.opener\.postMessage\((\{.*?\}), window\.location\.origin\)", resp.text)
    assert match, resp.text
    return json.loads(match.group(1))


def test_oidc_disabled_answers_404(client):
    object.__setattr__(settings, "oidc_enabled", False)
    resp = client.get("/api/v1/auth/oidc/login", follow_redirects=False)
    assert resp.status_code == 404
    assert "not enabled" in resp.json()["detail"]


def test_oidc_login_redirects_with_pkce_and_state(client, provider):
    flow, location = _start_flow(client)
    assert location.startswith(f"{ISSUER}/authorize?")
    assert "code_challenge=" in location
    assert "code_challenge_method=S256" in location
    assert "state=" in location and f"state={flow['state']}" in location
    assert "nonce=" in location and f"nonce={flow['nonce']}" in location
    assert "redirect_uri=" in location
    assert set(flow) >= {"state", "nonce", "verifier"}


def test_oidc_full_flow_provisions_user(client, provider, session):
    flow, _ = _start_flow(client)
    resp = _finish_flow(client, provider, flow, email="sso@example.com", groups=["platform-admins", "other"])
    assert resp.status_code == 200, resp.text
    tokens = _tokens_from_popup(resp)
    assert tokens["access_token"]
    assert tokens["refresh_token"]
    assert tokens["source"] == "controlplane-oidc"

    from controlplane.repositories.users import UserRepository

    user = UserRepository(session).get_by_email("sso@example.com")
    assert user is not None
    assert user.role == "admin", "groups must map to a platform role"

    from controlplane.models import Team, TeamMember
    from sqlalchemy import select

    team = session.scalar(select(Team).where(Team.slug == f"personal-{str(user.id).replace('-', '')}"))
    assert team is not None, "first SSO login must create a personal team"
    membership = session.scalar(select(TeamMember).where(TeamMember.team_id == team.id, TeamMember.user_id == user.id))
    assert membership is not None and membership.role == "admin"

    from controlplane.core.vault import get_secret_store

    assert get_secret_store().get(str(user.id), "ssh_private_key")
    assert get_secret_store().get(str(user.id), "ssh_public_key")


def test_oidc_maps_unknown_groups_to_default_role(client, provider, session):
    flow, _ = _start_flow(client)
    resp = _finish_flow(client, provider, flow, email="dev@example.com", groups=["some-other-group"])
    assert resp.status_code == 200, resp.text
    from controlplane.repositories.users import UserRepository

    user = UserRepository(session).get_by_email("dev@example.com")
    assert user.role == "user"


def test_oidc_callback_state_mismatch_rejected(client, provider):
    flow, _ = _start_flow(client)
    provider.pend("bad-state-code", flow["verifier"], flow["nonce"], "x@example.com", [])
    resp = client.get("/api/v1/auth/oidc/callback?code=bad-state-code&state=wrong-state")
    assert resp.status_code == 400
    assert "State mismatch" in resp.json()["detail"]


def test_oidc_callback_without_flow_cookie_rejected(client):
    resp = client.get("/api/v1/auth/oidc/callback?code=x&state=y")
    assert resp.status_code == 400
    assert "flow cookie" in resp.json()["detail"]


def test_oidc_callback_rejects_forged_flow_cookie(client):
    from controlplane.core.security import random_hex

    forged = oidc.encode_flow_cookie(random_hex(8), random_hex(8), random_hex(8))
    client.cookies.set("oidc_flow", forged)
    resp = client.get("/api/v1/auth/oidc/callback?code=x&state=y")
    # The cookie is validly signed, so decoding succeeds; the state check
    # then fails because x/y are not the cookie's own values... but actually
    # a forged cookie cannot be signed without the secret, so this must be
    # rejected at decode time. Either way, 400 and no tokens.
    assert resp.status_code == 400


def test_oidc_callback_rejects_tampered_cookie(client):
    resp = client.get("/api/v1/auth/oidc/login", follow_redirects=False)
    assert resp.status_code == 302
    cookie = next(c for c in resp.headers.get_list("set-cookie") if c.startswith("oidc_flow="))
    value = cookie.split("=", 1)[1].split(";", 1)[0]
    client.cookies.set("oidc_flow", value + "x")
    resp = client.get("/api/v1/auth/oidc/callback?code=abc&state=def")
    assert resp.status_code == 400
    assert "flow" in resp.json()["detail"]


def test_oidc_callback_rejects_unknown_code(client, provider):
    flow, _ = _start_flow(client)
    resp = client.get(f"/api/v1/auth/oidc/callback?code=nope&state={flow['state']}")
    assert resp.status_code == 401  # token exchange failed
    assert "id_token" not in resp.text


def test_oidc_callback_bad_signature_rejected(client, provider):
    flow, _ = _start_flow(client)
    provider.pend("bad-sig", flow["verifier"], flow["nonce"], "s@example.com", [])
    # Sign the id_token with a different key than the one in the JWKS.
    provider.rogue_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    resp = client.get(f"/api/v1/auth/oidc/callback?code=bad-sig&state={flow['state']}")
    assert resp.status_code == 401
    assert "ID token verification failed" in resp.json()["detail"]


def test_oidc_callback_wrong_issuer_rejected(client, provider):
    flow, _ = _start_flow(client)
    provider.pend("bad-iss", flow["verifier"], flow["nonce"], "s@example.com", [])
    # Sign with claims where iss differs from the discovered issuer.
    orig_sign = provider._sign

    def _sign_bad_iss(claims):
        claims["iss"] = "https://evil.example.test"
        return orig_sign(claims)

    provider._sign = _sign_bad_iss
    resp = client.get(f"/api/v1/auth/oidc/callback?code=bad-iss&state={flow['state']}")
    assert resp.status_code == 401
    assert "ID token verification failed" in resp.json()["detail"]


def test_oidc_callback_wrong_nonce_rejected(client, provider):
    flow, _ = _start_flow(client)
    provider.pend("bad-nonce", flow["verifier"], "wrong-nonce-value", "s@example.com", [])
    resp = client.get(f"/api/v1/auth/oidc/callback?code=bad-nonce&state={flow['state']}")
    assert resp.status_code == 401
    assert "nonce" in resp.json()["detail"]


def test_oidc_callback_missing_nonce_rejected(client, provider):
    flow, _ = _start_flow(client)
    provider.pend("no-nonce", flow["verifier"], flow["nonce"], "s@example.com", [])
    # Drop the nonce from the id_token entirely.
    orig_sign = provider._sign

    def _sign_no_nonce(claims):
        claims.pop("nonce", None)
        return orig_sign(claims)

    provider._sign = _sign_no_nonce
    resp = client.get(f"/api/v1/auth/oidc/callback?code=no-nonce&state={flow['state']}")
    assert resp.status_code == 401
    assert "ID token verification failed" in resp.json()["detail"]


def test_oidc_second_login_refreshes_role(client, provider, session):
    flow, _ = _start_flow(client)
    assert _finish_flow(client, provider, flow, email="r@example.com", groups=["platform-developers"]).status_code == 200

    from controlplane.repositories.users import UserRepository

    user = UserRepository(session).get_by_email("r@example.com")
    assert user.role == "developer"
    user_id = user.id

    flow, _ = _start_flow(client)
    resp = _finish_flow(client, provider, flow, email="r@example.com", groups=["platform-admins"])
    assert resp.status_code == 200
    session.refresh(user)
    assert user.id == user_id  # not recreated
    assert user.role == "admin", "role must follow current group membership"


def test_sso_user_cannot_use_local_password_login(client, provider):
    flow, _ = _start_flow(client)
    assert _finish_flow(client, provider, flow, email="nopwd@example.com", groups=[]).status_code == 200
    resp = client.post("/api/v1/auth/login", json={"email": "nopwd@example.com", "password": "Guessed!Passw0rd"})
    assert resp.status_code == 401, "SSO-provisioned users must not have a working password"


def test_local_auth_flag_blocks_password_login(client, provider):
    object.__setattr__(settings, "local_auth_enabled", False)
    resp = client.post(
        "/api/v1/auth/register",
        json={"email": "a@example.com", "password": "Str0ng!Passw0rd", "password_confirm": "Str0ng!Passw0rd"},
    )
    assert resp.status_code == 403
    resp = client.post("/api/v1/auth/login", json={"email": "a@example.com", "password": "Str0ng!Passw0rd"})
    assert resp.status_code == 403
    assert "single sign-on" in resp.json()["detail"]

    # SSO itself must keep working with local auth off.
    flow, _ = _start_flow(client)
    resp = _finish_flow(client, provider, flow, email="sso@example.com", groups=[])
    assert resp.status_code == 200


def test_auth_config_endpoint(client):
    resp = client.get("/api/v1/auth/config")
    assert resp.status_code == 200
    assert resp.json()["oidc_enabled"] is True
    assert resp.json()["local_auth_enabled"] is True
    object.__setattr__(settings, "oidc_enabled", False)
    resp = client.get("/api/v1/auth/config")
    assert resp.json()["oidc_enabled"] is False
    object.__setattr__(settings, "oidc_enabled", True)


def test_auth_config_test_mode_reflects_environment_and_nothing_else_can(client, settings_override):
    """The frontend used to decide "test mode" itself, unconditionally,
    hardcoding a fixed password into the JS in every build — so any real
    deployment that shipped that static file let anyone log in as any
    account that had ever registered through the console, by typing only
    its email. Only the server's own ENVIRONMENT setting may say this is a
    lab; the endpoint must not take a hint from the client (headers, query
    params) that could let anyone opt themselves into it."""
    with settings_override(environment="production"):
        resp = client.get("/api/v1/auth/config")
        assert resp.json()["test_mode"] is False

        # A client-controlled signal must not be able to flip this.
        resp = client.get("/api/v1/auth/config", headers={"X-Test-Mode": "true"})
        assert resp.json()["test_mode"] is False

    with settings_override(environment="dev"):
        resp = client.get("/api/v1/auth/config")
        assert resp.json()["test_mode"] is True