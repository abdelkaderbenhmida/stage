# migrations/versions

Alembic revision files, applied in order. Current head is `0011`.

- `0001_initial.py` — base schema.
- `0002_teams_ttl_webhooks_pool.py` — teams, project TTL, webhook subscriptions, warm pool.
- `0003_rollout_strategy.py` — deployment rollout strategy column.
- `0004_request_id.py` — request-id tracking on jobs.
- `0005_team_id_required.py` — makes `team_id` non-nullable on projects.
- `0006_deployment_config.py` — deployment config column.
- `0007_one_active_deploy_per_deployment.py` — uniqueness constraint: at most one active
  deploy job per deployment.
- `0008_deployment_service_unique.py` — uniqueness constraint on deployment service name.
- `0009_job_steps.py` — adds the `job_step` table backing per-step pipeline status.
- `0010_health_path_defaults_to_tcp.py` — health check path/probe default change.
- `0011_every_user_is_admin.py` — user role migration.

Each revision must define both `upgrade()` and `downgrade()`, and downgrade must
actually reverse the change — this is exercised by the migration tests.
