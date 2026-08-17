"""A deployment may have only one deploy job in flight, and its history is indexed.

Two deploys of the same service race: both build, both push a tag under the
same `<team>/<project>-<service>` prefix, and both run `kubectl apply` against
the same manifests. The one that lands last wins, and which one that is
depends on worker scheduling — so clicking redeploy twice, or a webhook
firing while a deploy is already running, could roll out the older commit.

A check in the API is not enough: two requests can both read "nothing
running" before either inserts its row. The partial unique index below is the
only place the rule cannot be raced.

`ix_jobs_deployment_id_created_at` supports the other half: listing one
deployment's job history newest-first without scanning the whole table.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Rows predating the rule can already violate it, and CREATE UNIQUE INDEX
    # would fail on them. A duplicate active deploy is a race that has already
    # happened: keep the newest and record the rest as interrupted, which is
    # the same status a worker killed mid-task ends up with.
    op.execute(
        """
        UPDATE jobs SET status = 'interrupted',
                        error_message = COALESCE(
                            error_message,
                            'superseded by a newer deploy of the same deployment'
                        )
        WHERE type = 'deploy'
          AND status IN ('queued', 'running')
          AND deployment_id IS NOT NULL
          AND id NOT IN (
              SELECT DISTINCT ON (deployment_id) id
              FROM jobs
              WHERE type = 'deploy'
                AND status IN ('queued', 'running')
                AND deployment_id IS NOT NULL
              ORDER BY deployment_id, created_at DESC
          )
        """
    )
    op.create_index(
        "uq_jobs_active_deploy_per_deployment",
        "jobs",
        ["deployment_id"],
        unique=True,
        postgresql_where=sa.text("type = 'deploy' AND status IN ('queued', 'running')"),
    )
    op.create_index(
        "ix_jobs_deployment_id_created_at", "jobs", ["deployment_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_deployment_id_created_at", table_name="jobs")
    op.drop_index("uq_jobs_active_deploy_per_deployment", table_name="jobs")
