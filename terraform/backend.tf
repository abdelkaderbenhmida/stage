# Terraform state backend.
#
# This project runs entirely on the local machine: libvirt/KVM VMs, no cloud
# account, no cloud state bucket. State therefore lives in a local file.
#
# Incident 8 / section 8.1 of the cahier des charges asks for a backend with
# locking, because two people running `terraform apply` at once corrupt the
# state. `backend "local"` gives no locking, so the rule here is the one a
# single-operator homelab can actually keep: never run two applies against
# this directory at the same time. The control plane enforces that for
# workspaces it renders itself — it serialises Terraform jobs per project.
#
# terraform.tfstate stays gitignored regardless. It contains the cloud-init
# SSH public key in cleartext, and re-adding it to the repo is what caused
# Incident 8 in the first place.
#
# If you ever do need shared state without taking on a cloud dependency, the
# control plane can render a per-project `backend "http"` block pointing at a
# self-hosted state server (see `state_url` in
# controlplane/renderers/terraform.py). That path keeps everything local too.
#
# Provider upgrade safety: when renaming or moving `libvirt_domain.node`
# (or any tracked resource), always wrap in a `moved {}` block so a future
# `terraform apply` does not destroy + recreate VMs. Example:
#   moved {
#     from = libvirt_domain.cluster_nodes
#     to   = libvirt_domain.node
#   }

terraform {
  backend "local" {
    path = "terraform.tfstate"
  }
}
