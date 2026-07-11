terraform {
  required_version = "~> 1.5" # P2: upper-bound to avoid surprise breaking jumps.

  required_providers {
    libvirt = {
      source  = "dmacvicar/libvirt"
      version = "~> 0.9"
    }

    local = {
      source  = "hashicorp/local"
      version = "~> 2.5"
    }
  }
}

provider "libvirt" {
  uri = var.libvirt_uri
}

locals {
  ssh_public_key = var.ssh_public_key != null && trimspace(var.ssh_public_key) != "" ? trimspace(var.ssh_public_key) : (
    can(file(pathexpand("~/.ssh/id_ed25519.pub"))) ? trimspace(file(pathexpand("~/.ssh/id_ed25519.pub"))) : (
      can(file(pathexpand("~/.ssh/id_rsa.pub"))) ? trimspace(file(pathexpand("~/.ssh/id_rsa.pub"))) : ""
    )
  )

  network_prefix = var.network_prefix != null ? var.network_prefix : tonumber(split("/", var.network_cidr)[1])
  gateway_ip     = var.gateway_ip != null ? var.gateway_ip : cidrhost(var.network_cidr, 1)


  master_node = {
    name = var.master_name
    ip   = cidrhost(var.network_cidr, 10)
    role = "master"
  }

  worker_nodes = [
    for index in range(var.worker_count) : {
      name = format("worker-%02d", index + 1)
      ip   = cidrhost(var.network_cidr, 11 + index)
      role = "worker"
    }
  ]

  nodes = {
    for node in concat([local.master_node], local.worker_nodes) : node.name => node
  }
}

resource "libvirt_network" "platform" {
  name = var.network_name

  domain = {
    name       = var.network_domain
    local_only = "yes"
  }

  forward = {
    mode = "nat"
  }

  ips = [
    {
      address = cidrhost(var.network_cidr, 1)
      netmask = cidrnetmask(var.network_cidr)
      dhcp = {
        ranges = [
          {
            start = cidrhost(var.network_cidr, 100)
            end   = cidrhost(var.network_cidr, 200)
          }
        ]
      }
    }
  ]

  dns = {
    enable = "yes"
    forwarders = [
      for dns in var.dns_servers : { addr = dns }
    ]
  }
}

resource "libvirt_volume" "base" {
  name = var.base_image_name
  pool = var.storage_pool

  target = {
    format = { type = "qcow2" }
  }

  create = {
    content = {
      url = var.base_image_path
    }
  }
}

resource "libvirt_volume" "node" {
  for_each = local.nodes

  name     = "${each.key}.qcow2"
  pool     = var.storage_pool
  capacity = var.disk_size_gb * 1024 * 1024 * 1024

  create = {
    content = {
      url = var.base_image_path
    }
  }

  target = {
    format = { type = "qcow2" }
    permissions = {
      owner = var.libvirt_volume_owner_uid
      group = var.libvirt_volume_group_gid
      mode  = "0600"
    }
  }


}

resource "libvirt_cloudinit_disk" "init" {
  for_each = local.nodes

  name = "${each.key}-init.iso"

  user_data = templatefile("${path.module}/cloud-init.tpl", {
    hostname       = each.key
    ssh_user       = var.ssh_user
    ssh_public_key = local.ssh_public_key
  })

  meta_data = yamlencode({
    instance-id    = each.key
    local-hostname = each.key
  })

  network_config = templatefile("${path.module}/network-config.tpl", {
    interface_name = var.network_interface
    ip             = each.value.ip
    prefix         = local.network_prefix
    gateway        = local.gateway_ip
    dns_servers    = var.dns_servers
  })
}

resource "libvirt_volume" "cloudinit_iso" {
  for_each = local.nodes

  name = "${each.key}-cloudinit.iso"
  pool = var.storage_pool

  create = {
    content = {
      url = libvirt_cloudinit_disk.init[each.key].path
    }
  }
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

resource "libvirt_domain" "node" {
  for_each = local.nodes

  name        = each.key
  type        = "kvm"
  memory      = var.vm_memory_mb
  memory_unit = "MiB"
  vcpu        = var.vm_vcpu

  os = {
    type         = "hvm"
    type_arch    = "x86_64"
    type_machine = "q35"
  }

  devices = {
    disks = [
      {
        source = {
          volume = {
            pool   = libvirt_volume.node[each.key].pool
            volume = libvirt_volume.node[each.key].name
          }
        }
        target = {
          dev = "vda"
          bus = "virtio"
        }
        driver = {
          type = "qcow2"
        }
      },
      {
        device = "cdrom"
        source = {
          volume = {
            pool   = libvirt_volume.cloudinit_iso[each.key].pool
            volume = libvirt_volume.cloudinit_iso[each.key].name
          }
        }
        target = {
          dev = "sda"
          bus = "sata"
        }
      },
    ]

    interfaces = [
      {
        model = { type = "virtio" }
        source = {
          network = {
            network = libvirt_network.platform.name
          }
        }
      },
    ]

    consoles = [
      {
        type        = "pty"
        target_port = "0"
        target_type = "serial"
      },
    ]

    graphics = [
      {
        vnc = {
          auto_port = true
          listen    = "127.0.0.1"
        }
      },
    ]
  }

  running = true
}



resource "local_file" "ansible_inventory" {
  filename = "${path.module}/inventory.ini"
  content = templatefile("${path.module}/inventory.tpl", {
    master_name = local.master_node.name
    master_ip   = local.master_node.ip
    workers     = local.worker_nodes
    ssh_user    = var.ssh_user
  })
}
