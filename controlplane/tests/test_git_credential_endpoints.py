"""HTTP behaviour of the git-credential endpoints.

These exist because module-level tests passed while both write endpoints
returned 500 in the running application: `audit()` was called with the wrong
signature and `check_rate_limit()` with the wrong arity. Neither is reachable
by testing the credential module on its own — only by going through the router.
"""

import uuid

import pytest


def _team_id(client, auth) -> str:
    """The caller's own team (a personal team exists from registration)."""
    resp = client.get("/api/v1/teams", headers=auth)
    assert resp.status_code == 200, resp.text
    return resp.json()[0]["id"]


@pytest.mark.integration
def test_full_lifecycle_over_http(auth_headers, client):
    auth = auth_headers("gitcred-owner@example.com")
    team = _team_id(client, auth)

    assert client.get(f"/api/v1/teams/{team}/git-credential", headers=auth).json()["configured"] is False

    stored = client.put(
        f"/api/v1/teams/{team}/git-credential",
        json={"token": "ghs_abcdefghijklmnopqrstuvwx"},
        headers=auth,
    )
    assert stored.status_code == 200, stored.text
    assert stored.json()["configured"] is True

    assert client.get(f"/api/v1/teams/{team}/git-credential", headers=auth).json()["configured"] is True

    removed = client.delete(f"/api/v1/teams/{team}/git-credential", headers=auth)
    assert removed.status_code == 200, removed.text
    assert removed.json()["configured"] is False


@pytest.mark.integration
def test_no_response_ever_carries_the_token(auth_headers, client):
    """There must be no way to read a stored credential back out."""
    auth = auth_headers("gitcred-secret@example.com")
    team = _team_id(client, auth)
    token = "ghs_uniquevalue1234567890abc"

    client.put(f"/api/v1/teams/{team}/git-credential", json={"token": token}, headers=auth)

    for resp in (
        client.get(f"/api/v1/teams/{team}/git-credential", headers=auth),
        client.get("/api/v1/teams", headers=auth),
    ):
        assert token not in resp.text, f"token echoed back by {resp.url}"


@pytest.mark.integration
def test_a_stranger_cannot_touch_another_teams_credential(auth_headers, client):
    """A non-member must not learn the team exists, let alone write to it."""
    owner = auth_headers("gitcred-a@example.com")
    team = _team_id(client, owner)

    stranger = auth_headers("gitcred-b@example.com")
    assert client.get(f"/api/v1/teams/{team}/git-credential", headers=stranger).status_code == 404
    assert client.put(
        f"/api/v1/teams/{team}/git-credential",
        json={"token": "ghs_abcdefghijklmnopqrstuvwx"},
        headers=stranger,
    ).status_code == 404
    assert client.delete(f"/api/v1/teams/{team}/git-credential", headers=stranger).status_code == 404


@pytest.mark.integration
def test_unauthenticated_access_is_rejected(client):
    team = uuid.uuid4()
    assert client.get(f"/api/v1/teams/{team}/git-credential").status_code == 401
    assert client.put(
        f"/api/v1/teams/{team}/git-credential", json={"token": "ghs_abcdefghijklmnopqrstuvwx"}
    ).status_code == 401


@pytest.mark.integration
@pytest.mark.parametrize("token", ["", "   ", "short", "ghs_ok\nFORGED=pwned"])
def test_unusable_tokens_are_refused_with_a_client_error(auth_headers, client, token):
    """Never a 500: a newline would forge a variable in the sandbox env-file."""
    auth = auth_headers("gitcred-bad@example.com")
    team = _team_id(client, auth)

    resp = client.put(f"/api/v1/teams/{team}/git-credential", json={"token": token}, headers=auth)
    assert resp.status_code in (400, 422), f"got {resp.status_code}: {resp.text}"
