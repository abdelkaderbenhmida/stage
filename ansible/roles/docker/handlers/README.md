# handlers

Handlers for the `docker` role, triggered by `notify:` from tasks that change containerd's
configuration.

- `main.yml` — `Restart containerd`: runs `systemd` restart with `daemon_reload: true`.
  Notified when `tasks/main.yml` writes `/etc/containerd/config.toml` or edits the
  `SystemdCgroup` line in it, so the new config takes effect without a full role rerun.
