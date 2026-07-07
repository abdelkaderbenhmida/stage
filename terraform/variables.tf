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
  description = "Path to the Ubuntu cloud image used as the VM base disk."
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
  description = "Private network CIDR for all nodes."
  type        = string
  default     = "192.168.56.0/24"
}

variable "network_prefix" {
  description = "CIDR prefix length used by cloud-init networking."
  type        = number
  default     = 24
}

variable "gateway_ip" {
  description = "Gateway IP used by the private network."
  type        = string
  default     = "192.168.56.1"
}

variable "dns_servers" {
  description = "DNS resolvers configured on the nodes."
  type        = list(string)
  default     = ["1.1.1.1", "8.8.8.8"]
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
}

variable "vm_vcpu" {
  description = "vCPU count allocated to each VM."
  type        = number
  default     = 2
}

variable "vm_memory_mb" {
  description = "Memory allocated to each VM in MB."
  type        = number
  default     = 2048
}

variable "disk_size_gb" {
  description = "Disk size allocated to each VM in GB."
  type        = number
  default     = 20
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
}