#cloud-config
hostname: ${hostname}
manage_etc_hosts: true

users:
  - name: ${ssh_user}
    groups: [sudo]
    shell: /bin/bash
    sudo: ALL=(ALL) NOPASSWD:ALL
    ssh_authorized_keys:
      - ${ssh_public_key}

growpart:
  mode: auto
  devices: [/]
  ignore_growroot_disabled: false

resizefs:
  enabled: true

package_update: true
package_upgrade: false