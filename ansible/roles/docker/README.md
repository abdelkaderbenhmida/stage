# docker

Installs Docker CE, the CLI, and containerd on Ubuntu nodes (jammy/noble), configures
containerd's cgroup driver for kubeadm compatibility, and adds the `devops` user to the
`docker` group. Runs on every host, before the Kubernetes roles.

- `tasks/main.yml` — adds the Docker apt repo (GPG key dearmored into
  `/etc/apt/keyrings/docker.gpg`), installs `docker-ce`/`docker-ce-cli`/`containerd.io`
  (unpinned — exact version pins were dropped from the upstream repo), enables the
  `docker`/`containerd` services, generates `/etc/containerd/config.toml` via
  `containerd config default` and flips `SystemdCgroup = true` in it.
- `defaults/main.yml` — package list, keyring/config paths, and the hardcoded
  `docker_user: devops`.
- `handlers/main.yml` — `Restart containerd`, notified after the config file is written or
  edited.
- `meta/main.yml` — Galaxy role metadata (author, supported platforms, min Ansible 2.14).
- `molecule/` — Molecule test scenario for this role.
