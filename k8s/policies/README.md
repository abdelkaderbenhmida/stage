# k8s/policies

Cluster-wide guard-rails enforced at admission (Kyverno) and, separately, a
Rego policy set for CI-time manifest checks (`conftest`). These are additional
to, not a replacement for, the Pod Security Admission `restricted` labels set
on the `devops-platform` and `monitoring` namespaces.

- `disallow-latest-images.yaml` — two Kyverno `ClusterPolicy` objects:
  `require-image-digest-pin` (Deployments must reference images by
  `@sha256:...` digest, not a mutable tag) and `restrict-security-context`
  (drop all Linux capabilities, `runAsNonRoot: true`). Both currently
  `validationFailureAction: Audit`, meant to be promoted to `Enforce` once
  production has migrated.
- `expired-namespace-cleanup.yaml` — a Kyverno `ClusterCleanupPolicy` backstop
  only, not the source of truth for project expiry (that's
  `controlplane.workers.tasks.reap_expired_projects`). Deletes a tenant
  Namespace if its `platform.devops/expires-at` label is more than 24h past
  due — the case where the app-level reaper failed to run. Marked
  UNVERIFIED in its header: the JMESPath time comparison has not been tested
  against a live CleanupController, so validate before relying on it.
- `conftest/` — Rego policies for CI-time (not admission-time) checks.

Install: `helm install kyverno kyverno/kyverno -n kyverno --create-namespace`
then `kubectl apply -f k8s/policies/` (the `ClusterCleanupPolicy` needs the
Kyverno CleanupController, bundled since v1.9).
