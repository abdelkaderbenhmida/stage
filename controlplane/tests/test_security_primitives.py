"""Security primitives: Argon2id hashing, JWT lifecycle, refresh-token digests."""

import jwt
from controlplane.core.security import (
    create_access_token,
    decode_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)


def test_password_hash_roundtrip():
    h = hash_password("hunter2-SuperSecret!")
    assert h != "hunter2-SuperSecret!"
    assert verify_password("hunter2-SuperSecret!", h)


def test_wrong_password_rejected():
    h = hash_password("correct horse")
    assert not verify_password("battery staple", h)


def test_hash_is_salted():
    h1 = hash_password("same-password")
    h2 = hash_password("same-password")
    assert h1 != h2


def test_access_token_roundtrip():
    token = create_access_token("user-123", role="admin")
    payload = decode_access_token(token)
    assert payload["sub"] == "user-123"
    assert payload["role"] == "admin"
    assert payload["type"] == "access"


def test_tampered_token_rejected():
    token = create_access_token("user-123")
    broken = token[:-3] + "abc"
    assert decode_access_token(broken) is None


def test_token_from_another_secret_rejected():
    from controlplane.core.config import settings

    token = jwt.encode({"sub": "x", "type": "access"}, "different-secret-0123456789abcdef", algorithm="HS256")
    assert decode_access_token(token) is None
    assert settings.jwt_secret != "different-secret-0123456789abcdef"


def test_garbage_token_rejected():
    assert decode_access_token("not.a.jwt") is None


def test_refresh_token_is_opaque_and_digested():
    t1 = generate_refresh_token()
    t2 = generate_refresh_token()
    assert len(t1) >= 32
    assert t1 != t2
    digest = hash_refresh_token(t1)
    assert digest == hash_refresh_token(t1)
    assert digest != t1
    assert digest != hash_refresh_token(t2)
