# Terraform state backend.
#
# devops-analysis-report.md P0 #4: the previous `backend "local"` config
# stored terraform.tfstate (containing the cloud-init SSH public key in
# cleartext) inside the repo, with no locking. Two paths are supported:
#
#   Remote (recommended for any team / CI environment):
#     Uncomment the S3 backend block below, create a dedicated bucket with
#     versioning + default encryption (SSE-KMS) + a DynamoDB table named
#     `terraform-locks` for state locking. Then run:
#         terraform init -backend-config="bucket=<your-bucket>" \
#                       -backend-config="key=stage/terraform.tfstate" \
#                       -backend-config="region=<your-region>"
#
#   Local (single-user dev / homelab only):
#     Leave the local backend active. Ensure terraform.tfstate is gitignored
#     (see repo-root .gitignore) and never commit it. No locking is provided —
#     take care to not run `terraform apply` concurrently on the same host.
#
# Either way, terraform.tfstate MUST be gitignored (see repo .gitignore).

terraform {
  # Local dev backend — switch to S3 below for any team / CI use.
  backend "local" {
    path = "terraform.tfstate"
  }

  # S3 backend — uncomment for remote state + locking. Requires:
  #   - bucket with versioning enabled, SSE-KMS encryption, public access blocked
  #   - DynamoDB table "terraform-locks" with a LockID primary key
  #   - bucket policy granting least-privilege access to deploy identities
  # backend "s3" {
  #   bucket         = "stage-terraform-state"
  #   key            = "stage/terraform.tfstate"
  #   region         = "eu-west-1"
  #   dynamodb_table = "terraform-locks"
  #   encrypt        = true
  #   acl            = "private"
  # }
}
