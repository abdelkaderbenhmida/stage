"""Per-tenant git credentials for cloning private repositories.

The deploy pipeline clones a tenant's repository over HTTPS with no
credentials, so a private repository cannot be deployed at all. This module
adds the missing credential without weakening the two controls that make the
clone safe today.

Design decisions, and why:

**HTTPS tokens, not SSH deploy keys.** Deploy keys are the obvious answer and
are perfectly respectable, but ``core/repo_url.py`` allows only the ``https``
scheme and explicitly rejects ``ssh://``, ``git://`` and ``file://``. That
allowlist is a deliberate control against scheme confusion and SSRF. Adopting
SSH means weakening it for every repository, including public ones. A token
authenticates over HTTPS, so the allowlist is untouched. Deploy keys also
scale badly for a tenant with several services: one key per repository, each
pasted into a separate settings page.

**The token is scoped to the team, never the user.** A project belongs to a
team (Phase 1 made ``team_id`` the only boundary), and the credential is
resolved from the project's team at clone time. A job therefore cannot reach
another tenant's credential even if it is handed another tenant's repository
URL, and the credential survives one member leaving.

**It is write-only through the API.** There is no read endpoint. The platform
can use a credential it holds but nobody can retrieve it, so a stolen session
cannot exfiltrate the token that guards the tenant's source.

**It is never written where it can be copied.** A token embedded in the clone
URL — the usual shortcut — is persisted by git into ``.git/config`` inside the
checkout. The very next pipeline step is ``docker build`` on that directory,
so a ``COPY . .`` in the tenant's Dockerfile bakes the credential into an
image that is then pushed to a registry. This module never puts the token in
a URL; it supplies it through an askpass helper that reads it from the
environment, and the caller removes ``.git`` before building.

**Short-lived tokens are preferred.** A GitHub App installation token expires
in an hour and is scoped to the repositories the tenant selected; a
fine-grained PAT is a long-lived credential and should be scoped to
``contents: read`` on specific repositories. Both are stored and used
identically here, so the choice is policy rather than code.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from controlplane.core.vault import get_secret_store

# One entry per team. The store is keyed by "user_id" in its own vocabulary;
# we pass the team id, which is what tenancy is actually scoped by.
_SECRET_KEY = "git_token"

# GitHub accepts any username when the password is a token, but this specific
# one is what it documents for App installation tokens, and it is harmless for
# a PAT.
GITHUB_TOKEN_USERNAME = "x-access-token"


@dataclass(frozen=True)
class GitCredential:
    """A resolved credential, ready to hand to a clone."""

    username: str
    token: str

    def askpass_env(self) -> dict[str, str]:
        """Environment for git's askpass helper.

        Returned separately from the non-secret environment so the caller can
        route it through the sandbox's ``secret_env``, which keeps values out
        of the ``docker run`` argv.
        """
        return {"GIT_USERNAME": self.username, "GIT_PASSWORD": self.token}


# git calls the askpass program once for the username and once for the
# password, passing a human-readable prompt as its only argument. Matching on
# "Username" is how the helper tells the two apart.
ASKPASS_SCRIPT = """#!/bin/sh
case "$1" in
  *[Uu]sername*) printf '%s' "$GIT_USERNAME" ;;
  *) printf '%s' "$GIT_PASSWORD" ;;
esac
"""


def set_team_token(team_id: str, token: str, username: str = GITHUB_TOKEN_USERNAME) -> None:
    """Store (or replace) the git token for a team."""
    token = token.strip()
    if not token:
        raise ValueError("Token must not be empty.")
    if "\n" in token or "\r" in token:
        # Would break the env-file the sandbox writes, and no real token
        # contains one.
        raise ValueError("Token must not contain line breaks.")
    store = get_secret_store()
    store.set(str(team_id), _SECRET_KEY, f"{username}:{token}")


def delete_team_token(team_id: str) -> None:
    get_secret_store().delete(str(team_id), _SECRET_KEY)


def has_team_token(team_id: str) -> bool:
    """Whether a credential exists, without revealing it.

    This is what the API may expose: the tenant needs to know whether they
    have configured access, but never what the value is.
    """
    return get_secret_store().get(str(team_id), _SECRET_KEY) is not None


def get_team_credential(team_id: str) -> GitCredential | None:
    raw = get_secret_store().get(str(team_id), _SECRET_KEY)
    if not raw:
        return None
    username, _, token = raw.partition(":")
    if not token:
        return None
    return GitCredential(username=username, token=token)


def credential_for_repo(team_id: str, repo_url: str) -> GitCredential | None:
    """The credential to use for ``repo_url``, if one applies.

    Returns None for a repository that needs no credential, so a public clone
    stays exactly as unauthenticated as it is today. Sending a token to a host
    that did not require it is how tokens end up in other people's logs.
    """
    host = (urlparse(repo_url).hostname or "").lower()
    if host not in ("github.com", "gitlab.com"):
        return None
    return get_team_credential(team_id)
