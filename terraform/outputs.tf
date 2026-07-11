output "master_ip" {
  description = "IP address of the master node."
  value       = local.master_node.ip
  sensitive   = true # IPs no longer echoed in plain `terraform plan` output.
}

output "worker_ips" {
  description = "IP addresses of the worker nodes."
  value       = [for worker in local.worker_nodes : worker.ip]
  sensitive   = true
}

output "node_ips" {
  description = "All node names and their IP addresses."
  value       = { for name, node in local.nodes : name => node.ip }
  sensitive   = true
}

output "ansible_inventory_file" {
  description = "Generated Ansible inventory file path."
  value       = local_file.ansible_inventory.filename
  sensitive   = true # generated positional artifact; do not surface in logs.
}

output "ansible_inventory_content" {
  description = "Rendered Ansible inventory content (contains IPs + user)."
  value       = local_file.ansible_inventory.content
  sensitive   = true
}
