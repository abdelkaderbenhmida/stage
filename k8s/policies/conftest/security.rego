package main

# Enforce readOnlyRootFilesystem on all containers
deny[msg] {
  input.kind == "Deployment"
  container := input.spec.template.spec.containers[_]
  not container.securityContext.readOnlyRootFilesystem
  msg := sprintf("Container %s in %s must set readOnlyRootFilesystem: true", [container.name, input.metadata.name])
}

# Enforce drop ALL capabilities
deny[msg] {
  input.kind == "Deployment"
  container := input.spec.template.spec.containers[_]
  caps := container.securityContext.capabilities.drop[_]
  caps != "ALL"
  msg := sprintf("Container %s in %s must drop ALL capabilities", [container.name, input.metadata.name])
}

# Enforce runAsNonRoot
deny[msg] {
  input.kind == "Deployment"
  input.spec.template.spec.securityContext.runAsNonRoot != true
  msg := sprintf("Deployment %s must set runAsNonRoot: true", [input.metadata.name])
}
