"""§7 item 8 — CSP and security headers must ship on every response.

The SPA is same-origin with the API and buildless, and every interactive
element dispatches through a ``data-act`` attribute table rather than an
inline handler, so script-src needs no 'unsafe-inline' exception anywhere.

style-src does carry one: both halves of the console render data-driven
markup that sets ``style="width:NN%"`` and similar on the element, which no
static stylesheet can express. That is a formatting concern, not a script
execution one, and script-src staying strict is what the last test guards.

HSTS is prod-only: dev traffic runs over plain HTTP on localhost.

frame-src is the one host exception in the policy: the operator console embeds
Grafana/Prometheus/Alertmanager/Kibana through a backend-managed
`kubectl port-forward` on an ephemeral 127.0.0.1 port, which is a different
origin from the API's own and so is not covered by 'self'. Loopback with a
port wildcard is the entire widening — no external host is ever framed.
"""

import re

import pytest

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; connect-src 'self'; "
    "frame-src 'self' http://127.0.0.1:* https://127.0.0.1:*; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
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
    script_src = next(d for d in policy.split("; ") if d.startswith("script-src"))
    assert script_src == "script-src 'self'"
    assert "unsafe-eval" not in policy


def test_no_inline_event_handlers_in_shipped_assets():
    """The strict script-src above is only safe while the assets stay clean:
    one `onclick="…"` slipping back in would silently no-op in the browser."""
    from controlplane.web.router import STATIC_DIR

    offenders = [
        path.relative_to(STATIC_DIR)
        for path in STATIC_DIR.rglob("*")
        if path.suffix in {".js", ".html"}
        and re.search(r"\son(?:click|keydown|change|input|submit)\s*=", path.read_text())
    ]
    assert offenders == []
