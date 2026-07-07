output "master_ip" {
  description = "IP address of the master node."
  value       = local.master_node.ip
}

output "worker_ips" {
  description = "IP addresses of the worker nodes."
  value       = [for worker in local.worker_nodes : worker.ip]
}

output "node_ips" {
  description = "All node names and their IP addresses."
  value       = { for name, node in local.nodes : name => node.ip }
}

output "ansible_inventory_file" {
  description = "Generated Ansible inventory file."
  value       = local_file.ansible_inventory.filename
}

output "ansible_inventory_content" {
  description = "Rendered Ansible inventory content."
  value       = local_file.ansible_inventory.content
}