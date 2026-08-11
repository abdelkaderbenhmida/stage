[masters]
${master_name} ansible_host=${master_public_ip} private_ip=${master_private_ip} ansible_user=${ssh_user}

[workers]
%{ for worker in workers ~}
${worker.name} ansible_host=${worker.public_ip} private_ip=${worker.private_ip} ansible_user=${ssh_user}
%{ endfor ~}

[all:vars]
ansible_python_interpreter=/usr/bin/python3