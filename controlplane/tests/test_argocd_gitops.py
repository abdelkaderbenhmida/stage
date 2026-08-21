"""ArgoCD tenant objects: the destination must be pinned to the project's own
namespace, and the AppProject must not whitelist anything else.

These are tenancy assertions, not rendering assertions. A change that makes an
Application syncable into a namespace its team does not own is the failure this
file exists to catch.
"""

import uuid

import pytest
from controlplane.core.validation import k8s_namespace
from controlplane.renderers.argocd import (
    app_project_name,
    application_name,
    manifest_path,
    render_app_project,
    render_application,
)

REPO = "git://git-server.gitops.svc.cluster.local:9418/tenants.git"


def test_application_destination_is_the_projects_own_namespace():
    project_id, team_id = uuid.uuid4(), uuid.uuid4()
    app = render_application(project_id, team_id, "api", REPO, "main")

    assert app["spec"]["destination"]["namespace"] == k8s_namespace(project_id)
    assert app["spec"]["source"]["path"] == manifest_path(project_id, "api")
    assert app["spec"]["project"] == app_project_name(team_id)


def test_two_teams_naming_a_service_alike_get_distinct_applications():
    """The whole point of keying on project id rather than project name: two
    teams both running "staging/api" must not collide onto one Application in
    the shared argocd namespace, where the second sync would take over the
    first's resources."""
    a, b = uuid.uuid4(), uuid.uuid4()

    assert application_name(a, "api") != application_name(b, "api")
    assert manifest_path(a, "api") != manifest_path(b, "api")


def test_app_project_whitelists_only_that_teams_namespaces():
    team_id = uuid.uuid4()
    owned = [uuid.uuid4(), uuid.uuid4()]
    stranger = uuid.uuid4()

    project = render_app_project(team_id, owned, REPO)
    allowed = {destination["namespace"] for destination in project["spec"]["destinations"]}

    assert allowed == {k8s_namespace(project_id) for project_id in owned}
    assert k8s_namespace(stranger) not in allowed


def test_app_project_allows_only_the_platform_manifest_repo():
    """A tenant able to point an Application at their own repository could sync
    manifests the platform never rendered — and therefore never scanned."""
    project = render_app_project(uuid.uuid4(), [uuid.uuid4()], REPO)

    assert project["spec"]["sourceRepos"] == [REPO]


def test_app_project_grants_no_cluster_scoped_resources():
    """Namespace, ResourceQuota and NetworkPolicy belong to provisioning. An
    Application that could create a Namespace could create one without any of
    the limits the tenant is supposed to be bound by."""
    project = render_app_project(uuid.uuid4(), [uuid.uuid4()], REPO)

    assert project["spec"]["clusterResourceWhitelist"] == []


def test_application_does_not_create_its_namespace():
    """CreateNamespace=true would produce a bare namespace with no quota, no
    LimitRange and no default-deny NetworkPolicy — the tenant's workload would
    run entirely unbounded."""
    app = render_application(uuid.uuid4(), uuid.uuid4(), "api", REPO, "main")

    assert "CreateNamespace=true" not in app["spec"]["syncPolicy"]["syncOptions"]


def test_application_carries_the_cascade_finalizer():
    """Without it, deleting the Application orphans the workload: pods keep
    running and consuming the tenant's quota with nothing tracking them."""
    app = render_application(uuid.uuid4(), uuid.uuid4(), "api", REPO, "main")

    assert "resources-finalizer.argocd.argoproj.io" in app["metadata"]["finalizers"]


@pytest.mark.parametrize("service", ["a" * 60, "api", "x"])
def test_application_name_is_a_valid_object_name(service):
    """Kubernetes rejects names over 63 characters, and a rejected apply fails
    the whole deploy — a long service name must be truncated, not passed on."""
    name = application_name(uuid.uuid4(), service)

    assert 0 < len(name) <= 63
    assert not name.endswith("-")


def test_secrets_are_never_committed_to_the_manifest_repository(tmp_path, monkeypatch):
    """A git history keeps a secret forever, readable to anyone who can read
    the repo, and survives every later rotation. The deploy path applies the
    rendered Secret directly instead, so it must never be copied in here."""
    from controlplane.runners import gitops

    project_id = uuid.uuid4()
    manifests = []
    for name in ("deployment.yaml", "service.yaml", "secret.yaml"):
        path = tmp_path / name
        path.write_text(f"# {name}\n")
        manifests.append(path)

    staged: list[list[str]] = []

    def fake_sandbox(run):
        # Snapshot what is on disk at the moment git is asked to stage it.
        if run.command[:4] == ["git", "-C", "/workspace/repo", "add"]:
            target = run.workspace / "repo" / gitops.manifest_path(project_id, "api")
            staged.append(sorted(child.name for child in target.iterdir()))
        return gitops.SandboxResult(exit_code=0, output="abc123def456", duration_seconds=0.0)

    monkeypatch.setattr(gitops, "run_sandbox", fake_sandbox)

    sha = gitops.publish_manifests(
        project_id,
        "api",
        manifests,
        message="deploy",
        config=gitops.GitOpsConfig(repo_url="git://example/t.git", branch="main", username="u", password="p"),
    )

    assert staged == [["deployment.yaml", "service.yaml"]]
    assert sha == "abc123def456"


def test_publish_refuses_when_no_manifest_repository_is_configured():
    """Better than pushing nowhere and reporting the deploy as shipped."""
    from controlplane.runners import gitops

    with pytest.raises(gitops.GitOpsError, match="GITOPS_REPO_URL"):
        gitops.publish_manifests(
            uuid.uuid4(), "api", [], message="deploy",
            config=gitops.GitOpsConfig(repo_url="", branch="main", username="", password=""),
        )


def test_publish_replaces_the_previous_render_rather_than_merging(tmp_path, monkeypatch):
    """A manifest the renderer stopped producing — a Rollout after switching
    back to a plain Deployment — must disappear from git, or both objects stay
    live in the cluster fighting over the same pods."""
    from controlplane.runners import gitops

    project_id = uuid.uuid4()
    manifest = tmp_path / "deployment.yaml"
    manifest.write_text("# deployment\n")

    staged: list[list[str]] = []

    def fake_sandbox(run):
        target = run.workspace / "repo" / gitops.manifest_path(project_id, "api")
        if run.command[:2] == ["git", "clone"]:
            # Simulate the previous render already present in the repository.
            target.mkdir(parents=True, exist_ok=True)
            (target / "rollout.yaml").write_text("# stale\n")
        if run.command[:4] == ["git", "-C", "/workspace/repo", "add"]:
            staged.append(sorted(child.name for child in target.iterdir()))
        return gitops.SandboxResult(exit_code=0, output="sha", duration_seconds=0.0)

    monkeypatch.setattr(gitops, "run_sandbox", fake_sandbox)
    gitops.publish_manifests(
        project_id, "api", [manifest], message="deploy",
        config=gitops.GitOpsConfig(repo_url="git://example/t.git", branch="main", username="u", password="p"),
    )

    assert staged == [["deployment.yaml"]]


def _project(mode):
    from controlplane.models import Project

    project = Project()
    project.id = uuid.uuid4()
    project.team_id = uuid.uuid4()
    project.infra_spec = {"mode": mode}
    return project


def test_gitops_is_off_unless_configured():
    """Turning it on without a reachable manifest repository fails every
    deployment at the publish step, so it must not default to on."""
    from controlplane.tests.conftest import override_settings
    from controlplane.workers import tasks

    with override_settings(gitops_enabled=False):
        assert tasks._gitops_applies_to(_project("namespace")) is False


def test_gitops_never_applies_to_a_vm_mode_project():
    """A VM-mode project runs on its own cluster, which has no ArgoCD in it.
    An Application shipped there is an object nothing reads, and the workload
    would simply never appear."""
    from controlplane.tests.conftest import override_settings
    from controlplane.workers import tasks

    with override_settings(gitops_enabled=True):
        assert tasks._gitops_applies_to(_project("vm")) is False
        assert tasks._gitops_applies_to(_project("namespace")) is True


def test_argocd_url_falls_back_to_the_worker_url_but_not_the_reverse():
    """Defaulting the worker's push URL to an in-cluster name instead would
    produce a push that cannot resolve and a deploy that fails at the last
    step."""
    from controlplane.core.config import settings
    from controlplane.tests.conftest import override_settings

    with override_settings(
        gitops_repo_url="git://node:30418/tenants.git", gitops_repo_url_internal=""
    ):
        assert settings.gitops_repo_url_for_argocd == "git://node:30418/tenants.git"

    with override_settings(
        gitops_repo_url="git://node:30418/tenants.git",
        gitops_repo_url_internal="git://svc:9418/tenants.git",
    ):
        assert settings.gitops_repo_url_for_argocd == "git://svc:9418/tenants.git"
        assert settings.gitops_repo_url == "git://node:30418/tenants.git"
