# Terraform state backend.
#
# Production contract (Incident 8 / section 8.1 of the cahier des charges): the
# state MUST live in a remote backend with locking. The previous `backend
# "local"` config stored terraform.tfstate (containing the cloud-init SSH
# public key in cleartext) inside the repo, with no locking — two engineers
# running `terraform apply` concurrently corrupt the state (Incident 8).
#
# Active backend: S3 + DynamoDB locking. Bucket/key/region are passed via
# `terraform init -backend-config=...` so the same code runs in CI (AWS OIDC
# federation) and on a workstation with short-lived creds. Override file
# `terraform.backend.tfvars.example` documents the variables.
#
#   terraform init \
#     -backend-config="bucket=stage-terraform-state" \
#     -backend-config="key=stage/terraform.tfstate" \
#     -backend-config="region=eu-west-1"
#
# CI uses AWS OIDC federation (preferred) instead of long-lived AWS keys:
#   - Configure an IAM role that trusts `token.actions.githubusercontent.com`
#   - With `sub=repo:<owner>/<repo>:environment:production`
#   - The role grants: s3:PutObject/GetObject on the state bucket +
#     dynamodb:PutItem/GetItem/Scan/Query on terraform-locks.
#
# Homelab fallback (single-user only): comment the S3 block and uncomment the
# local block below. terraform.tfstate MUST stay gitignored either way.
#
# Provider upgrade safety: when renaming or moving `libvirt_domain.node`
# (or any tracked resource), always wrap in a `moved {}` block so a future
# `terraform apply` does not destroy + recreate VMs in production. Example:
#   moved {
#     from = libvirt_domain.cluster_nodes
#     to   = libvirt_domain.node
#   }

terraform {
  # Remote state with locking — required for any team / CI use (Incident 8).
  # backend "s3" {
  #   bucket         = "stage-terraform-state"
  #   key            = "stage/terraform.tfstate"
  #   region         = "eu-west-1"
  #   dynamodb_table = "terraform-locks"
  #   encrypt        = true
  #   acl            = "private"
  # }

  # Local fallback — homelab single-user only. No locking provided: never run
  # `terraform apply` concurrently on the same host. State stays gitignored.
  backend "local" {
    path = "terraform.tfstate"
  }
}
