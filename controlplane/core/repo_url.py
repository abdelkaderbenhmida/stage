"""Repository URL allowlist validation (docs/PLATFORM_SPEC.md §7.5).

``repo_url`` is passed to ``git clone`` and must be restricted to https with
an allowlisted host. This blocks SSRF against internal addresses and local
file disclosure.
"""

from urllib.parse import urlparse

ALLOWED_SCHEMES = ("https",)
ALLOWED_HOSTS = ("github.com", "gitlab.com")


class InvalidRepoUrl(ValueError):
    pass


def validate_repo_url(repo_url: str) -> str:
    parsed = urlparse(repo_url.strip())
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise InvalidRepoUrl(
            f"Repository URL scheme must be https (got {parsed.scheme!r}). "
            "git://, ssh:// and file:// are not allowed."
        )
    host = (parsed.hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        raise InvalidRepoUrl(
            f"Repository host {host!r} is not in the allowlist "
            f"({', '.join(ALLOWED_HOSTS)})."
        )
    return repo_url.strip()
