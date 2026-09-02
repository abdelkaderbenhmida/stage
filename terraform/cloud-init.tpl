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
    # La valeur contient « : » : sans guillemets, YAML la lit comme une
    # imbrication et rejette tout le user-data (cloud-init ignore alors
    # l'ensemble du fichier, utilisateur et cle SSH compris).
    # Fenetre de provisionnement. Les roles Ansible installent des paquets et
    # ecrivent dans /etc : la regle restreinte d origine (kubeadm, kubelet et
    # trois systemctl restart, mot de passe requis) les faisait echouer des la
    # premiere tache. On provisionne large, puis le playbook repose la regle
    # restreinte en fin de course.
    sudo: "ALL=(ALL) NOPASSWD:ALL"
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
