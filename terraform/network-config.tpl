version: 2
ethernets:
  ${interface_name}:
    dhcp4: false
    addresses:
      - ${ip}/${prefix}
    gateway4: ${gateway}
    nameservers:
      addresses:
%{ for dns in dns_servers ~}
        - ${dns}
%{ endfor ~}
