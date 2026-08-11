# Terraform state backend.
#
# Production contract (Incident 8 / section 8.1 of the cahier des charges): the
# state MUST live in a remote backend with locking. The previous `backend
# "local"` config stored terraform.tfstate (containing the SSH public key in
# cleartext) inside the repo, with no locking — two engineers running
# `terraform apply` concurrently corrupt the state (Incident 8).
#
# Active backend (cloud-version / AWS): S3 + DynamoDB locking. Bucket/key/region
# are passed via `terraform init -backend-config=...` so the same code runs in CI
# (AWS OIDC federation) and on a workstation with short-lived creds. Example:
#
#   # one-time bootstrap — bucket + lock table must exist first:
#   aws s3api create-bucket --bucket devops-platform-tfstate \
#     --region eu-west-1 --create-bucket-configuration LocationConstraint=eu-west-1
#   aws dynamodb create-table --table-name terraform-locks \
#     --attribute-definitions AttributeName=LockID,AttributeType=S \
#     --key-schema AttributeName=LockID,KeyType=HASH \
#     --billing-mode PAY_PER_REQUEST
#
#   terraform init \
#     -backend-config="bucket=devops-platform-tfstate" \
#     -backend-config="key=devops-platform/terraform.tfstate" \
#     -backend-config="region=eu-west-1"
#
# CI uses AWS OIDC federation (preferred) instead of long-lived AWS keys:
#   - IAM role trusting `token.actions.githubusercontent.com`
#   - `sub=repo:<owner>/<repo>:environment:production`
#   - Role grants: s3:PutObject/GetObject on the state bucket +
#     dynamodb:PutItem/GetItem/Scan/Query on terraform-locks.
#
# Local single-user fallback (homelab): comment the S3 block and uncomment the
# local block below. terraform.tfstate MUST stay gitignored either way.

terraform {
  # Remote state with locking — required for any team / CI use (Incident 8).
  backend "s3" {
    bucket         = "devops-platform-tfstate"
    key            = "devops-platform/terraform.tfstate"
    region         = "eu-west-1"
    dynamodb_table = "terraform-locks"
    encrypt        = true
  }

  # Local fallback — homelab single-user only. No locking provided: never run
  # `terraform apply` concurrently on the same host. State stays gitignored.
  # backend "local" {
  #   path = "terraform.tfstate"
  # }
}
