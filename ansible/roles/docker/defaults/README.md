# defaults

Default variables for the `docker` role, used when it's run standalone (outside the main
playbook) and overridable via `group_vars`/`host_vars`.

- `main.yml` — `docker_packages` (docker-ce, docker-ce-cli, containerd.io),
  `docker_keyring_dir` / `docker_gpg_key_path` (apt keyring locations),
  `containerd_config_path`, and `docker_user: devops` (the account added to the `docker`
  group).
