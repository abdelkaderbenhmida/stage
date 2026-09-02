package main

import rego.v1

# Deployments allowed to run with readOnlyRootFilesystem: false
# Vault dev mode writes to /vault/data; Grafana writes to /var/lib/grafana/dashboards
allow_readonly_root_fs_exceptions = {
    "vault": {"vault"},
    "monitoring": {"grafana"},
}

# Deployments allowed to run as root (runAsNonRoot: false)
# Vault dev mode requires root for IPC_LOCK + privileged port 8200
allow_run_as_root_exceptions = {
    "vault": {"vault"},
}

# Enforce readOnlyRootFilesystem on all containers
deny contains msg if {
    input.kind == "Deployment"
    container := input.spec.template.spec.containers[_]
    not container.securityContext.readOnlyRootFilesystem
    not input.metadata.namespace in object.keys(allow_readonly_root_fs_exceptions)
    msg := sprintf("Container %s in %s must set readOnlyRootFilesystem: true", [container.name, input.metadata.name])
}
deny contains msg if {
    input.kind == "Deployment"
    container := input.spec.template.spec.containers[_]
    not container.securityContext.readOnlyRootFilesystem
    namespace := input.metadata.namespace
    namespace in object.keys(allow_readonly_root_fs_exceptions)
    not container.name in allow_readonly_root_fs_exceptions[namespace]
    msg := sprintf("Container %s in %s must set readOnlyRootFilesystem: true", [container.name, input.metadata.name])
}

# Enforce drop ALL capabilities
deny contains msg if {
    input.kind == "Deployment"
    container := input.spec.template.spec.containers[_]
    caps := container.securityContext.capabilities.drop[_]
    caps != "ALL"
    msg := sprintf("Container %s in %s must drop ALL capabilities", [container.name, input.metadata.name])
}

# Enforce runAsNonRoot
deny contains msg if {
    input.kind == "Deployment"
    input.spec.template.spec.securityContext.runAsNonRoot != true
    not input.metadata.namespace in object.keys(allow_run_as_root_exceptions)
    msg := sprintf("Deployment %s must set runAsNonRoot: true", [input.metadata.name])
}
deny contains msg if {
    input.kind == "Deployment"
    input.spec.template.spec.securityContext.runAsNonRoot != true
    namespace := input.metadata.namespace
    namespace in object.keys(allow_run_as_root_exceptions)
    not input.metadata.name in allow_run_as_root_exceptions[namespace]
    msg := sprintf("Deployment %s must set runAsNonRoot: true", [input.metadata.name])
}
