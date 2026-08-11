terraform {
  required_version = "~> 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }

    local = {
      source  = "hashicorp/local"
      version = "~> 2.5"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

locals {
  ssh_public_key = var.ssh_public_key != null && trimspace(var.ssh_public_key) != "" ? trimspace(var.ssh_public_key) : (
    can(file(pathexpand("~/.ssh/id_ed25519.pub"))) ? trimspace(file(pathexpand("~/.ssh/id_ed25519.pub"))) : (
      can(file(pathexpand("~/.ssh/id_rsa.pub"))) ? trimspace(file(pathexpand("~/.ssh/id_rsa.pub"))) : ""
    )
  )

  master_node = {
    name = var.master_name
    role = "master"
  }

  worker_nodes = [
    for index in range(var.worker_count) : {
      name = format("worker-%02d", index + 1)
      role = "worker"
    }
  ]
}

# ── VPC & Networking ──
resource "aws_vpc" "platform" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = {
    Name        = "devops-platform-vpc"
    Environment = var.environment
  }
}

resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.platform.id
  cidr_block              = var.subnet_cidr
  map_public_ip_on_launch = true

  tags = {
    Name        = "devops-platform-subnet"
    Environment = var.environment
  }
}

resource "aws_internet_gateway" "gw" {
  vpc_id = aws_vpc.platform.id

  tags = {
    Name        = "devops-platform-igw"
    Environment = var.environment
  }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.platform.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.gw.id
  }

  tags = {
    Name        = "devops-platform-rt"
    Environment = var.environment
  }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

# ── Security Group ──
resource "aws_security_group" "k8s" {
  name        = "devops-platform-sg"
  description = "Security group for Kubernetes cluster nodes"
  vpc_id      = aws_vpc.platform.id

  # Internal node-to-node communication (all protocols/ports)
  ingress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    self        = true
    description = "Allow all node-to-node communication"
  }

  # SSH access (restricted via var.admin_cidr_blocks)
  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = var.admin_cidr_blocks
    description = "Allow SSH access"
  }

  # Kubernetes API server
  ingress {
    from_port   = 6443
    to_port     = 6443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Allow Kubernetes API server"
  }

  # HTTP
  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Allow HTTP traffic"
  }

  # HTTPS
  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Allow HTTPS traffic"
  }

  # Kubernetes NodePorts range (30000-32767)
  ingress {
    from_port   = 30000
    to_port     = 32767
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Allow Kubernetes NodePorts"
  }

  # Egress: allow all outbound traffic
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Allow all outbound traffic"
  }

  tags = {
    Name        = "devops-platform-sg"
    Environment = var.environment
  }
}

# ── AMI Lookup (Ubuntu 22.04 LTS) ──
data "aws_ami" "ubuntu" {
  most_recent = true

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }

  owners = ["099720109477"] # Canonical
}

# ── SSH Key Pair ──
resource "aws_key_pair" "deployer" {
  key_name   = "devops-platform-key"
  public_key = local.ssh_public_key
}

resource "terraform_data" "ssh_key_guard" {
  input = local.ssh_public_key

  lifecycle {
    precondition {
      condition     = length(trimspace(local.ssh_public_key)) > 0
      error_message = "No SSH public key was found. Set var.ssh_public_key or create ~/.ssh/id_ed25519.pub (or ~/.ssh/id_rsa.pub) on this PC."
    }
  }
}

# ── EC2 Compute Instances ──
resource "aws_instance" "master" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.aws_instance_type_master
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.k8s.id]
  key_name               = aws_key_pair.deployer.key_name

  root_block_device {
    volume_size           = var.disk_size_gb
    volume_type           = "gp3"
    delete_on_termination = true
  }

  tags = {
    Name        = local.master_node.name
    Role        = "master"
    Environment = var.environment
  }
}

resource "aws_instance" "worker" {
  count                  = var.worker_count
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.aws_instance_type_worker
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.k8s.id]
  key_name               = aws_key_pair.deployer.key_name

  root_block_device {
    volume_size           = var.disk_size_gb
    volume_type           = "gp3"
    delete_on_termination = true
  }

  tags = {
    Name        = format("worker-%02d", count.index + 1)
    Role        = "worker"
    Environment = var.environment
  }
}

# ── Ansible Inventory Generation ──
resource "local_file" "ansible_inventory" {
  filename = "${path.module}/inventory.generated.ini"
  content = templatefile("${path.module}/inventory.tpl", {
    master_name       = local.master_node.name
    master_public_ip  = aws_instance.master.public_ip
    master_private_ip = aws_instance.master.private_ip
    workers = [
      for index, instance in aws_instance.worker : {
        name       = format("worker-%02d", index + 1)
        public_ip  = instance.public_ip
        private_ip = instance.private_ip
      }
    ]
    ssh_user = var.ssh_user
  })
}
