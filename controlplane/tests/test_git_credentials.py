"""Private-repository credentials: storage, scoping, and never leaking.

A token that lets the platform read a tenant's private source is the most
sensitive thing a tenant hands over, so these tests pin the properties that
make holding it defensible rather than the happy path.
"""

import pytest
from controlplane.core import git_credentials as gc
from controlplane.core.redaction import scrub_line


@pytest.fixture(autouse=True)
def _clean_store():
    yield
    for team in ("team-a", "team-b"):
        try:
            gc.delete_team_token(team)
        except Exception:
            pass


# --- scoping ---------------------------------------------------------------

def test_a_credential_is_scoped_to_one_team():
    """Team B must not be able to use Team A's token.

    This is the whole tenancy guarantee for private source: the clone resolves
    the credential from the project's own team, so handing a job another
    tenant's repository URL gets it nothing.
    """
    gc.set_team_token("team-a", "ghs_aaaaaaaaaaaaaaaaaaaaaaaa")

    assert gc.get_team_credential("team-a") is not None
    assert gc.get_team_credential("team-b") is None


def test_no_credential_is_sent_to_a_host_that_did_not_ask():
    """Sending a token to an unrelated host is how tokens end up elsewhere."""
    gc.set_team_token("team-a", "ghs_aaaaaaaaaaaaaaaaaaaaaaaa")

    assert gc.credential_for_repo("team-a", "https://github.com/o/r.git") is not None
    assert gc.credential_for_repo("team-a", "https://evil.example.net/o/r.git") is None


def test_a_team_without_a_credential_clones_anonymously():
    """Public repositories must stay exactly as unauthenticated as before."""
    assert gc.credential_for_repo("team-b", "https://github.com/o/r.git") is None


# --- shape of what we store ------------------------------------------------

def test_a_token_containing_a_newline_is_rejected():
    """The sandbox writes secrets to an env-file parsed one KEY=VALUE per line."""
    with pytest.raises(ValueError):
        gc.set_team_token("team-a", "ghs_good\nFORGED=pwned")


def test_an_empty_token_is_rejected():
    with pytest.raises(ValueError):
        gc.set_team_token("team-a", "   ")


def test_existence_is_reportable_without_revealing_the_value():
    assert gc.has_team_token("team-a") is False
    gc.set_team_token("team-a", "ghs_aaaaaaaaaaaaaaaaaaaaaaaa")
    assert gc.has_team_token("team-a") is True


# --- never in a URL --------------------------------------------------------

def test_the_credential_is_supplied_by_askpass_not_by_url():
    """A URL credential is persisted by git into .git/config in the checkout.

    The next pipeline step builds an image from that directory, so a
    `COPY . .` would bake the tenant's token into an image and push it to a
    registry. The askpass helper keeps the token out of the URL entirely.
    """
    gc.set_team_token("team-a", "ghs_aaaaaaaaaaaaaaaaaaaaaaaa")
    credential = gc.get_team_credential("team-a")

    env = credential.askpass_env()
    assert env["GIT_PASSWORD"] == "ghs_aaaaaaaaaaaaaaaaaaaaaaaa"
    assert "GIT_USERNAME" in env
    # The helper reads from the environment; it must not embed the value.
    assert "ghs_" not in gc.ASKPASS_SCRIPT


# --- never in a log --------------------------------------------------------

@pytest.mark.parametrize(
    "line",
    [
        "fatal: could not read from https://x-access-token:ghs_abcdefghij1234567890@github.com/o/r.git",
        "remote: token ghp_abcdefghijklmnopqrstuvwxyz0123456789 rejected",
        "using ghs_abcdefghij1234567890abcd for clone",
        "glpat-abcdefghij1234567890 failed",
    ],
)
def test_tokens_never_reach_a_job_log(line):
    assert scrub_line(line) == "[REDACTED]"


def test_a_tenant_named_repo_cannot_switch_scrubbing_off():
    """The allow-list works on whole lines and tenants choose repo names.

    A repository called "example.com" used to exempt every line mentioning it
    from scrubbing, token included.
    """
    line = "cloning https://github.com/org/example.com.git with ghs_abcdefghij1234567890abcd"
    assert scrub_line(line) == "[REDACTED]"
