package main

# Enforce readOnlyRootFilesystem on all containers
deny contains msg if {
  input.kind == "Deployment"
  container := input.spec.template.spec.containers[_]
  not container.securityContext.readOnlyRootFilesystem
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
  msg := sprintf("Deployment %s must set runAsNonRoot: true", [input.metadata.name])
}
