"""Per-tenant Elasticsearch access.

The assertions here are tenancy assertions. A change that lets one team's role
match another team's index pattern is the failure this file exists to catch.
"""

import uuid

import httpx
import pytest
from controlplane.core import elk_tenancy
from controlplane.core.validation import k8s_namespace


def test_role_grants_only_the_teams_own_namespaces():
    team_id = uuid.uuid4()
    owned = [uuid.uuid4(), uuid.uuid4()]
    stranger = uuid.uuid4()

    role = elk_tenancy.render_role(team_id, owned)
    patterns = role["indices"][0]["names"]

    assert patterns == sorted(f"tenant-{k8s_namespace(p)}-*" for p in owned)
    assert f"tenant-{k8s_namespace(stranger)}-*" not in patterns


def test_index_pattern_cannot_match_another_tenant():
    """The namespace is 20 hex characters with a fixed prefix, so one team's
    pattern cannot be a prefix of another's namespace."""
    a, b = uuid.uuid4(), uuid.uuid4()
    pattern_a = elk_tenancy.index_patterns([a])[0]

    assert not f"tenant-{k8s_namespace(b)}-2026.01.01".startswith(pattern_a.rstrip("*"))


def test_role_is_read_only():
    """A tenant that could write could forge or delete their own audit trail,
    which is the opposite of why logs are kept."""
    role = elk_tenancy.render_role(uuid.uuid4(), [uuid.uuid4()])

    assert sorted(role["indices"][0]["privileges"]) == ["read", "view_index_metadata"]


def test_role_grants_no_cluster_privileges():
    """`monitor` exposes cluster-wide index names, which would tell one tenant
    that another's namespaces exist."""
    role = elk_tenancy.render_role(uuid.uuid4(), [uuid.uuid4()])

    assert role["cluster"] == []


def test_a_team_with_no_projects_gets_a_role_that_grants_nothing():
    """Elasticsearch rejects an empty `names` list, so the indices block has to
    be absent rather than present-and-empty."""
    role = elk_tenancy.render_role(uuid.uuid4(), [])

    assert role["indices"] == []


def test_index_patterns_are_stable_across_calls():
    """An unordered list would make every provision look like a change and
    rewrite the role on every deploy."""
    ids = [uuid.uuid4() for _ in range(5)]

    assert elk_tenancy.index_patterns(ids) == elk_tenancy.index_patterns(list(reversed(ids)))


def test_space_disables_the_features_that_would_let_a_tenant_look_elsewhere():
    space = elk_tenancy.render_space(uuid.uuid4(), "Platform Team")

    for feature in ("dev_tools", "management", "indexPatterns"):
        assert feature in space["disabledFeatures"]


def _admin():
    return elk_tenancy.ElkAdmin(
        elasticsearch_url="http://es:9200", kibana_url="http://kibana:5601",
        username="elastic", password="pw",
    )


def _transport(recorder, space_status=404):
    def handler(request: httpx.Request) -> httpx.Response:
        recorder.append((request.method, str(request.url)))
        if "/api/spaces/space/" in str(request.url) and request.method == "GET":
            return httpx.Response(space_status)
        return httpx.Response(200, json={})

    return httpx.MockTransport(handler)


def test_provision_creates_space_role_and_user(monkeypatch):
    calls: list[tuple[str, str]] = []
    store = {}
    monkeypatch.setattr(
        elk_tenancy, "get_secret_store", lambda: _Store(store)
    )

    team_id = uuid.uuid4()
    with httpx.Client(transport=_transport(calls)) as client:
        password = elk_tenancy.provision_team(
            team_id, "Team", [uuid.uuid4()], admin=_admin(), client=client,
        )

    urls = [url for _, url in calls]
    assert any(url.endswith(f"/_security/role/{elk_tenancy.role_name(team_id)}") for url in urls)
    assert any(url.endswith(f"/_security/user/{elk_tenancy.user_name(team_id)}") for url in urls)
    assert any("/api/spaces/space" in url for url in urls)
    assert password


def test_provision_does_not_rotate_an_existing_password(monkeypatch):
    """Re-running must not invalidate a credential the platform already stored
    and is using on the tenant's behalf."""
    store = {}
    monkeypatch.setattr(elk_tenancy, "get_secret_store", lambda: _Store(store))
    team_id = uuid.uuid4()

    with httpx.Client(transport=_transport([])) as client:
        first = elk_tenancy.provision_team(team_id, "T", [uuid.uuid4()], admin=_admin(), client=client)
        second = elk_tenancy.provision_team(team_id, "T", [uuid.uuid4()], admin=_admin(), client=client)

    assert first == second


def test_provision_does_not_recreate_an_existing_space(monkeypatch):
    """Kibana's space API has no upsert: POST on an existing id is a 409."""
    store = {}
    monkeypatch.setattr(elk_tenancy, "get_secret_store", lambda: _Store(store))
    calls: list[tuple[str, str]] = []

    with httpx.Client(transport=_transport(calls, space_status=200)) as client:
        elk_tenancy.provision_team(uuid.uuid4(), "T", [uuid.uuid4()], admin=_admin(), client=client)

    assert not any(method == "POST" and "/api/spaces/space" in url for method, url in calls)


def test_provision_refuses_when_elasticsearch_is_not_configured():
    admin = elk_tenancy.ElkAdmin(elasticsearch_url="", kibana_url="", username="", password="")

    with pytest.raises(elk_tenancy.ElkProvisioningError, match="ELASTICSEARCH_URL"):
        elk_tenancy.provision_team(uuid.uuid4(), "T", [], admin=admin)


def test_an_elasticsearch_error_is_reported_not_swallowed(monkeypatch):
    """Silently continuing would leave a tenant with a Logs tab that returns
    nothing and no explanation anywhere."""
    store = {}
    monkeypatch.setattr(elk_tenancy, "get_secret_store", lambda: _Store(store))

    def handler(request):
        if "/api/spaces/space/" in str(request.url):
            return httpx.Response(404)
        return httpx.Response(403, text="insufficient privileges")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(elk_tenancy.ElkProvisioningError, match="403"):
            elk_tenancy.provision_team(uuid.uuid4(), "T", [uuid.uuid4()], admin=_admin(), client=client)


class _Store:
    def __init__(self, backing):
        self.backing = backing

    def get(self, user_id, key):
        return self.backing.get((user_id, key))

    def set(self, user_id, key, value):
        self.backing[(user_id, key)] = value
