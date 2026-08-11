output "master_public_ip" {
  description = "Public IP address of the Kubernetes master node."
  value       = aws_instance.master.public_ip
  sensitive   = true
}

output "master_private_ip" {
  description = "Private IP address of the Kubernetes master node."
  value       = aws_instance.master.private_ip
  sensitive   = true
}

output "worker_public_ips" {
  description = "Public IP addresses of the Kubernetes worker nodes."
  value = {
    for index, instance in aws_instance.worker :
    format("worker-%02d", index + 1) => instance.public_ip
  }
  sensitive = true
}

output "worker_private_ips" {
  description = "Private IP addresses of the Kubernetes worker nodes."
  value = {
    for index, instance in aws_instance.worker :
    format("worker-%02d", index + 1) => instance.private_ip
  }
  sensitive = true
}

output "ssh_master_command" {
  description = "SSH command to connect to the master node."
  value       = "ssh ${var.ssh_user}@${aws_instance.master.public_ip}"
  sensitive   = true
}

output "ansible_inventory_file" {
  description = "Generated Ansible inventory file path."
  value       = local_file.ansible_inventory.filename
  sensitive   = true
}

output "ansible_inventory_content" {
  description = "Rendered Ansible inventory content."
  value       = local_file.ansible_inventory.content
  sensitive   = true
}
