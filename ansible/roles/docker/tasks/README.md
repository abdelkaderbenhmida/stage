# tasks

The task list for the `docker` role.

- `main.yml` — installs apt prerequisites (ca-certificates, curl, gnupg, lsb-release),
  adds the Docker apt repository with a dearmored GPG key under
  `/etc/apt/keyrings/docker.gpg`, installs `docker-ce`/`docker-ce-cli`/`containerd.io`
  (version pins intentionally removed — see inline comment), enables and starts
  `docker`/`containerd`, adds the `devops` user to the `docker` group, generates
  `/etc/containerd/config.toml` from `containerd config default`, and sets
  `SystemdCgroup = true` in it via `lineinfile` (required for kubeadm compatibility).
  Config changes notify the `Restart containerd` handler. Every task is tagged `docker`.
