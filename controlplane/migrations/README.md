# migrations

Alembic migration environment for the control-plane database.

- `env.py` — reads `DATABASE_URL` from `controlplane.core.config.settings` (not from
  `alembic.ini` directly) and points at `controlplane.models.Base.metadata`.
- `script.py.mako` — template used by `alembic revision` to generate new revision files.
- `versions/` — the revision history.

Run from the `controlplane/` directory, since `alembic.ini`'s `script_location` is
relative to that file:

```bash
(cd controlplane && alembic upgrade head)
(cd controlplane && alembic downgrade -1)
```

New revisions are sequential: `NNNN_name.py` with `down_revision` pointing at the
current head. Both `upgrade` and `downgrade` must run clean.
