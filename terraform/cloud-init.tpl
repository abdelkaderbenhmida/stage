#cloud-config
hostname: ${hostname}
manage_etc_hosts: true

# P0/P1: fail-fast security defaults.
ssh_pwauth: false       # disable SSH password auth regardless of cloud image default.
disable_root: true      # never allow root SSH.

users:
  - name: ${ssh_user}
    groups: [sudo]
    shell: /bin/bash
    # Restricted sudo: kubeadm/kubelet/containerd commands. Password-required
    # for privilege escalation — NOPASSWD removed per security audit.
    sudo: ALL=(ALL) PASSWD: /usr/bin/kubeadm, /usr/bin/kubelet, /usr/bin/systemctl restart kubelet, /usr/bin/systemctl restart containerd, /usr/bin/systemctl restart docker
    lock_passwd: false        # password auth disabled above; account still usable via key.
    ssh_authorized_keys:
      - ${ssh_public_key}

growpart:
  mode: auto
  devices: [/]
  ignore_growroot_disabled: false

resizefs:
  enabled: true

package_update: true
package_upgrade: true  # P2: auto-upgrade existing packages to reduce drift.

# Explicit hardening on first boot.
runcmd:
  - [sed, -i, "s/^#*\\s*PermitRootLogin.*/PermitRootLogin no/",            /etc/ssh/sshd_config]
  - [sed, -i, "s/^#*\\s*PasswordAuthentication.*/PasswordAuthentication no/", /etc/ssh/sshd_config]
  - [sed, -i, "s/^#*\\s*PubkeyAuthentication.*/PubkeyAuthentication yes/",  /etc/ssh/sshd_config]
  - [sh, -c, "systemctl restart ssh sshd 2>/dev/null || systemctl restart sshd || true"]

# Fail2ban protects against brute-force over SSH once the pod is reachable.
packages:
  - fail2ban
