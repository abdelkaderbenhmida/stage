[masters]
${master_name} ansible_host=${master_ip} ansible_user=${ssh_user}

[workers]
%{ for worker in workers ~}
${worker.name} ansible_host=${worker.ip} ansible_user=${ssh_user}
%{ endfor ~}

[all:vars]
ansible_python_interpreter=/usr/bin/python3
