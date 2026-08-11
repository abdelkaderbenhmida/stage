variable "aws_region" {
  description = "AWS region for infrastructure provisioning."
  type        = string
  default     = "eu-west-1"
}

variable "environment" {
  description = "Deployment environment name (e.g. dev, staging, prod)."
  type        = string
  default     = "dev"
}

variable "vpc_cidr" {
  description = "CIDR block for the AWS VPC."
  type        = string
  default     = "10.0.0.0/16"
}

variable "subnet_cidr" {
  description = "CIDR block for the public subnet."
  type        = string
  default     = "10.0.1.0/24"
}

variable "master_name" {
  description = "Hostname and name tag for the master node EC2 instance."
  type        = string
  default     = "master-01"
}

variable "worker_count" {
  description = "Number of worker node EC2 instances to provision."
  type        = number
  default     = 2

  validation {
    condition     = var.worker_count >= 1 && var.worker_count <= 32
    error_message = "worker_count must be between 1 and 32."
  }
}

variable "aws_instance_type_master" {
  description = "AWS EC2 instance type for the Kubernetes master node."
  type        = string
  default     = "t3.medium"
}

variable "aws_instance_type_worker" {
  description = "AWS EC2 instance type for the Kubernetes worker nodes."
  type        = string
  default     = "t3.medium"
}

variable "disk_size_gb" {
  description = "EBS root volume size for each EC2 instance in GB."
  type        = number
  default     = 20

  validation {
    condition     = var.disk_size_gb >= 8 && var.disk_size_gb <= 1024
    error_message = "disk_size_gb must be between 8 and 1024."
  }
}

variable "ssh_user" {
  description = "Administrative SSH user created on Ubuntu AMI instances."
  type        = string
  default     = "ubuntu"
}

variable "ssh_public_key" {
  description = "SSH public key authorized on the EC2 instances. Leave null to auto-detect from ~/.ssh."
  type        = string
  default     = null
  nullable    = true
  sensitive   = true
}

variable "admin_cidr_blocks" {
  description = "CIDR blocks allowed SSH access to the nodes. Open to the world for the demo; restrict in production."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}
