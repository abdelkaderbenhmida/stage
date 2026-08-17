"""Deployments carry configuration: env vars, secret names, and a probe path.

Before this, a deployment was repo + branch + port + replicas and nothing
else, so the platform could only run applications that need no configuration
at all — no database URL, no feature flag, no log level.

`env_vars` holds non-secret configuration. `secret_keys` holds only the names
of secret variables; their values live in the secret store (Vault), because a
database row is the wrong place for a tenant's credentials and this table is
readable by anything that can see the deployment.

`health_path` was hardcoded to /livez in the manifest template, which quietly
required every tenant application to implement that exact endpoint.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "deployments",
        sa.Column("env_vars", postgresql.JSONB(), nullable=False, server_default="{}"),
    )
    op.add_column(
        "deployments",
        sa.Column("secret_keys", postgresql.JSONB(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "deployments",
        sa.Column("health_path", sa.String(200), nullable=False, server_default="/livez"),
    )


def downgrade() -> None:
    op.drop_column("deployments", "health_path")
    op.drop_column("deployments", "secret_keys")
    op.drop_column("deployments", "env_vars")
