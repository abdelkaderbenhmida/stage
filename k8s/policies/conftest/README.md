# k8s/policies/conftest

Rego policies for `conftest`-based CI checks against Deployment manifests —
distinct from the Kyverno `ClusterPolicy` objects one level up, which enforce
the same kind of rules at admission time in-cluster.

- `security.rego` — three `deny` rules against `input.kind == "Deployment"`:
  every container must set `securityContext.readOnlyRootFilesystem: true`,
  every container must drop the `ALL` capability, and the pod spec must set
  `securityContext.runAsNonRoot: true`.
