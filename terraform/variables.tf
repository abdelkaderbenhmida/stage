variable "libvirt_uri" {
  description = "Libvirt connection URI."
  type        = string
  default     = "qemu:///system"
}

variable "storage_pool" {
  description = "Libvirt storage pool that contains the base cloud image."
  type        = string
  default     = "default"
}

variable "base_image_name" {
  description = "Name to give the cloned base image inside the storage pool."
  type        = string
  default     = "ubuntu-base.qcow2"
}

variable "base_image_path" {
  description = "Path to the Ubuntu cloud image used as the VM base disk. Host-specific — override per libvirt host."
  type        = string
  default     = "/var/lib/libvirt/images/ubuntu-22.04-server-cloudimg-amd64.img"
}

variable "network_name" {
  description = "Private network name for the platform."
  type        = string
  default     = "devops-platform-net"
}

variable "network_domain" {
  description = "DNS domain assigned to the private network."
  type        = string
  default     = "devops.local"
}

variable "network_cidr" {
  description = "Private network CIDR for all nodes. Must be a valid IPv4 CIDR in RFC1918 space."
  type        = string
  default     = "192.168.56.0/24"

  validation {
    condition     = can(cidrhost(var.network_cidr, 0))
    error_message = "network_cidr must be a valid IPv4 CIDR (e.g. 192.168.56.0/24)."
  }

  validation {
    condition     = anytrue([for r in ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"] : can(cidrsubnet("${r}", 0, 0)) && can(cidrsubnet(var.network_cidr, 0, 0)) ? length(regexall(replace(r, "/.*$", ""), var.network_cidr)) > 0 : false])
    error_message = "network_cidr should be in RFC1918 private space (10/8, 172.16/12, or 192.168/16)."
  }
}

variable "network_prefix" {
  description = "CIDR prefix length used by cloud-init networking. Defaults to the prefix parsed from network_cidr."
  type        = number
  default     = 24

  validation {
    condition     = var.network_prefix > 0 && var.network_prefix <= 32
    error_message = "network_prefix must be an integer in 1..32."
  }
}

variable "gateway_ip" {
  description = "Gateway IP used by the private network. Defaults to .1 of network_cidr."
  type        = string
  default     = null
}

variable "dns_servers" {
  description = "DNS resolvers configured on the nodes and in the libvirt network forwarders."
  type        = list(string)
  default     = ["1.1.1.1", "8.8.8.8"]

  validation {
    condition     = length(var.dns_servers) >= 1
    error_message = "dns_servers must contain at least one resolver."
  }
}

variable "network_interface" {
  description = "Guest network interface name used by cloud-init."
  type        = string
  default     = "enp1s0"
}

variable "master_name" {
  description = "Hostname for the master node."
  type        = string
  default     = "master-01"
}

variable "worker_count" {
  description = "Number of worker nodes to provision."
  type        = number
  default     = 2

  validation {
    condition     = var.worker_count >= 1 && var.worker_count <= 32
    error_message = "worker_count must be between 1 and 32."
  }
}

variable "vm_vcpu" {
  description = "vCPU count allocated to each VM."
  type        = number
  default     = 2

  validation {
    condition     = var.vm_vcpu >= 1 && var.vm_vcpu <= 16
    error_message = "vm_vcpu must be between 1 and 16."
  }
}

variable "vm_memory_mb" {
  description = "Memory allocated to each VM in MB."
  type        = number
  default     = 2048

  validation {
    condition     = var.vm_memory_mb >= 512 && var.vm_memory_mb <= 32768
    error_message = "vm_memory_mb must be between 512 and 32768."
  }
}

variable "disk_size_gb" {
  description = "Disk size allocated to each VM in GB."
  type        = number
  default     = 20

  validation {
    condition     = var.disk_size_gb >= 8 && var.disk_size_gb <= 1024
    error_message = "disk_size_gb must be between 8 and 1024."
  }
}

variable "ssh_user" {
  description = "Administrative SSH user created by cloud-init."
  type        = string
  default     = "devops"
}

variable "ssh_public_key" {
  description = "SSH public key authorized on the nodes. Leave null to auto-detect a local key from ~/.ssh."
  type        = string
  default     = null
  nullable    = true
  sensitive   = true # P0 #4: do not echo in plan logs / state dumps.
}

variable "libvirt_volume_owner_uid" {
  description = "Numeric UID owning the libvirt volumes. Host-specific (libvirt user). Default matches Ubuntu/Debian libvirt-qemu."
  type        = number
  default     = 64055
}

variable "libvirt_volume_group_gid" {
  description = "Numeric GID owning the libvirt volumes. Host-specific (libvirt group). Default matches Ubuntu/Debian libvirt-qemu."
  type        = number
  default     = 993
}
