"""ArgoCD objects for tenant workloads — one AppProject per team, one
Application per deployment.

Why this exists: until now a tenant's manifests were applied by the worker
with ``kubectl apply`` and nothing reconciled them afterwards. Someone editing
a Deployment by hand, or a half-finished apply, left the cluster disagreeing
with what the platform believed it had shipped, and nothing noticed. ArgoCD
reconciles continuously, so drift is corrected instead of accumulating.

The tenancy rule this module has to hold up: **an Application must not be able
to name a destination outside its own project's namespace.** Two things enforce
that, and both matter because either alone is bypassable:

1. Every Application here is rendered with ``destination.namespace`` set from
   ``k8s_namespace(project.id)``. Nothing in it comes from user input.
2. The team's AppProject whitelists only the namespaces of projects that team
   owns. ArgoCD refuses to sync an Application whose destination is not in its
   project's list, so even a hand-edited Application in the argocd namespace
   cannot reach another tenant — the server-side check is the one that counts,
   because an attacker who can edit Applications can trivially edit (1).

The AppProject is therefore not decoration. Rendering Applications without it
would leave the whole boundary resting on a client-side string.
"""

from __future__ import annotations

import uuid

from controlplane.core.validation import k8s_namespace

# ArgoCD only reads Application/AppProject objects from its own namespace.
ARGOCD_NAMESPACE = "argocd"

# Kubernetes object names are limited to 63 characters (DNS-1123 label). The
# namespace prefix is a fixed 22 ("p-" + 20 hex), so a service name longer
# than 40 would silently produce an invalid object; truncating here is what
# keeps the API server from rejecting the whole apply.
_MAX_NAME = 63


def app_project_name(team_id: uuid.UUID) -> str:
    """The AppProject a team's Applications belong to.

    Keyed by team, not by project: a team routinely owns several projects, and
    one AppProject per project would multiply objects without adding a
    boundary — the boundary that matters is between *teams*.
    """
    return f"team-{team_id.hex[:20]}"


def application_name(project_id: uuid.UUID, service_name: str) -> str:
    """Globally unique Application name for one service in one project.

    Prefixed with the project's namespace rather than the project *name*, for
    the same reason ``k8s_namespace`` is: names are unique only per team, so
    two teams' "staging/api" would otherwise collide onto one Application in
    the shared argocd namespace — and the second one to sync would take over
    the first one's resources.
    """
    return f"{k8s_namespace(project_id)}-{service_name}"[:_MAX_NAME].rstrip("-")


def manifest_path(project_id: uuid.UUID, service_name: str) -> str:
    """Path inside the platform's manifest repository for this service.

    One directory per (project, service). The project component is the
    namespace, so a path cannot be steered into another tenant's directory by
    naming a project cleverly.
    """
    return f"{k8s_namespace(project_id)}/{service_name}"


def render_app_project(team_id: uuid.UUID, project_ids: list[uuid.UUID], repo_url: str) -> dict:
    """The AppProject scoping one team's Applications.

    ``project_ids`` is every project the team owns; the caller passes them
    from the repository layer, which is where the team boundary is already
    enforced. Passing a project the team does not own would widen the
    whitelist, so this function is never called with a caller-supplied list.
    """
    destinations = [
        {"namespace": k8s_namespace(project_id), "server": "https://kubernetes.default.svc"}
        for project_id in sorted(project_ids, key=str)
    ]
    return {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "AppProject",
        "metadata": {
            "name": app_project_name(team_id),
            "namespace": ARGOCD_NAMESPACE,
            "labels": {
                "app.kubernetes.io/managed-by": "controlplane",
                "controlplane.io/team": team_id.hex[:20],
            },
        },
        "spec": {
            "description": f"Tenant workloads for team {team_id}.",
            # Only the platform's own manifest repository. A tenant cannot
            # point an Application at an arbitrary repo, which would otherwise
            # let them sync manifests the platform never rendered or scanned.
            "sourceRepos": [repo_url],
            "destinations": destinations,
            # Deliberately empty: a tenant Application has no business creating
            # Namespaces, ClusterRoles or CRDs. The namespace itself, with its
            # quota and NetworkPolicy, is created by the provisioning path.
            "clusterResourceWhitelist": [],
            "namespaceResourceWhitelist": [
                {"group": "", "kind": "Service"},
                {"group": "", "kind": "Secret"},
                {"group": "", "kind": "ConfigMap"},
                {"group": "apps", "kind": "Deployment"},
                {"group": "networking.k8s.io", "kind": "Ingress"},
                {"group": "argoproj.io", "kind": "Rollout"},
                {"group": "argoproj.io", "kind": "AnalysisTemplate"},
            ],
        },
    }


def render_application(
    project_id: uuid.UUID,
    team_id: uuid.UUID,
    service_name: str,
    repo_url: str,
    revision: str,
) -> dict:
    """The Application that syncs one service's manifests into its namespace."""
    namespace = k8s_namespace(project_id)
    return {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "Application",
        "metadata": {
            "name": application_name(project_id, service_name),
            "namespace": ARGOCD_NAMESPACE,
            "labels": {
                "app.kubernetes.io/managed-by": "controlplane",
                "controlplane.io/team": team_id.hex[:20],
                "controlplane.io/project": namespace,
                "controlplane.io/service": service_name,
            },
            # Without the finalizer, deleting the Application orphans the
            # workload: the pods keep running in the tenant's namespace,
            # keep consuming their quota, and no longer appear anywhere in
            # the platform. Undeploy would stop meaning anything.
            "finalizers": ["resources-finalizer.argocd.argoproj.io"],
        },
        "spec": {
            "project": app_project_name(team_id),
            "source": {"repoURL": repo_url, "targetRevision": revision, "path": manifest_path(project_id, service_name)},
            "destination": {"server": "https://kubernetes.default.svc", "namespace": namespace},
            "syncPolicy": {
                "automated": {
                    "prune": True,
                    "selfHeal": True,
                    # A commit that renders no manifests must not be read as
                    # "delete everything this service has". That is a bug in
                    # the renderer, not an instruction from the tenant.
                    "allowEmpty": False,
                },
                # No CreateNamespace: the namespace carries the tenant's
                # ResourceQuota, LimitRange and default-deny NetworkPolicy. If
                # ArgoCD created it, it would create a *bare* one, and the
                # workload would land in a namespace with no limits at all.
                "syncOptions": ["PrunePropagationPolicy=foreground", "PruneLast=true", "ServerSideApply=true"],
                "retry": {"limit": 5, "backoff": {"duration": "5s", "factor": 2, "maxDuration": "3m"}},
            },
        },
    }
