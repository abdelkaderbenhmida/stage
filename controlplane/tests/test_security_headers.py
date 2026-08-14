"""§7 item 8 — CSP and security headers must ship on every response.

The SPA is same-origin with the API and buildless (no inline scripts), so
the Content-Security-Policy needs no 'unsafe-inline' exception. HSTS is
prod-only: dev traffic runs over plain HTTP on localhost.
"""

import pytest

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; "
    "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
    "base-uri 'self'; form-action 'self'"
)


@pytest.mark.parametrize("path", ["/", "/healthz", "/api/v1/projects"])
def test_security_headers_on_ui_and_api(client, path):
    response = client.get(path)
    assert response.status_code < 500
    assert response.headers["Content-Security-Policy"] == CSP
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    # Dev runs over plain HTTP; HSTS must not be sent there.
    assert "Strict-Transport-Security" not in response.headers


@pytest.mark.integration
def test_hsts_sent_in_production_like_env(client, settings_override):
    # is_dev is a property over environment; flip the underlying field.
    with settings_override(environment="production"):
        response = client.get("/")
        assert response.headers["Strict-Transport-Security"] == (
            "max-age=31536000; includeSubDomains"
        )


def test_csp_never_allows_inline_scripts(client):
    policy = client.get("/").headers["Content-Security-Policy"]
    assert "unsafe-inline" not in policy
    assert "unsafe-eval" not in policy
