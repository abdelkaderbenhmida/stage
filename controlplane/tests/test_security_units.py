"""Log scrubbing (§7.4) and repo URL allowlist (§7.5)."""

import pytest
from controlplane.core.redaction import scrub_line, scrub_stream
from controlplane.core.repo_url import InvalidRepoUrl, validate_repo_url


@pytest.mark.security
@pytest.mark.parametrize(
    "line",
    [
        "export AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE",
        "token = sk_live_abcdefghijklmnopqrstuvwxyz",
        "password: hunter2",
        "SECRET_KEY=hunter2",
        "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abcdefghijklmnopqrstuvwxyz.ABCDEFGHIJKLMNOP",
        "-----BEGIN OPENSSH PRIVATE KEY-----\n",
        "sshpass -p 'SuperSecret!' ansible-playbook site.yml",
        "GITHUB_TOKEN=ghp_0123456789abcdef0123456789abcdef012345",
    ],
)
def test_secret_lines_redacted(line):
    assert scrub_line(line) == "[REDACTED]"


@pytest.mark.parametrize(
    "line",
    [
        "password_hash = $argon2id$v=19$m=65536,t=3,p=4$abcdef",
        "hashed_password column migrated",
        "curl https://example.com/health",
        "plain log line, nothing to see",
        "ok",
    ],
)
def test_safe_lines_untouched(line):
    assert scrub_line(line) == line


def test_scrub_stream_generator():
    lines = iter(["password=supersecret", "normal", "AKIAIOSFODNN7EXAMPLE"])
    assert list(scrub_stream(lines)) == ["[REDACTED]", "normal", "[REDACTED]"]


@pytest.mark.security
@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/octocat/Hello-World.git",
        "https://gitlab.com/group/repo.git",
        "  https://github.com/org/repo  ",
    ],
)
def test_allowlisted_repo_urls_ok(url):
    assert validate_repo_url(url) == url.strip()


@pytest.mark.security
@pytest.mark.parametrize(
    "url",
    [
        "git@github.com:org/repo.git",          # ssh scheme
        "ssh://git@github.com/org/repo.git",     # ssh scheme
        "git://github.com/org/repo.git",         # git scheme
        "file:///etc/passwd",                    # local file
        "http://github.com/org/repo.git",        # http, not https
        "https://evil.com/repo.git",             # host not allowlisted
        "https://169.254.169.254/latest/meta-data",  # SSRF to cloud metadata
        "https://localhost/repo",                # SSRF to loopback
        "https://192.168.1.100/repo",            # SSRF to internal net
        "ftp://github.com/repo",                 # ftp
    ],
)
def test_non_allowlisted_repo_urls_rejected(url):
    with pytest.raises(InvalidRepoUrl):
        validate_repo_url(url)
