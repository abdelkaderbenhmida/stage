"""One row per service inside a project.

`create()` inserted unconditionally, so redeploying a service added another
row. One Kubernetes Deployment ended up described by several rows: the project
page listed the same service repeatedly with conflicting statuses (live and
blocked at once), and deleting one row tore down the workload the others still
claimed to own.

Existing duplicates are collapsed to the most recently created row before the
constraint goes on, since that is the one describing the current release.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Keep the newest row per (project_id, service_name); drop the rest.
    op.execute(
        sa.text(
            """
            DELETE FROM deployments d
            USING deployments newer
            WHERE d.project_id = newer.project_id
              AND d.service_name = newer.service_name
              AND (
                    d.created_at < newer.created_at
                 OR (d.created_at = newer.created_at AND d.id < newer.id)
              )
            """
        )
    )
    op.create_unique_constraint(
        "uq_deployments_project_service", "deployments", ["project_id", "service_name"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_deployments_project_service", "deployments", type_="unique")
