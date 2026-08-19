"""health_path defaults to empty (probe the port, not a path).

`/livez` is this platform's own internal service contract — every service in
its `app/` monorepo implements it. A tenant deploying their own application
has no reason to serve that exact path, but the probe was rendered against it
anyway: the app came up fine, the HTTP probe 404'd, liveness killed the
container, and a perfectly healthy app CrashLooped forever with no useful
signal about why.

The column default becomes empty, which the manifest templates render as a
TCP probe against the container port. Existing rows are deliberately left
alone: a deployment already running with an explicit `/livez` is a deployment
whose app really does serve it, and rewriting that to a weaker TCP probe
would silently reduce its health checking.

Revision ID: 0010
Revises: 0009
"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "deployments",
        "health_path",
        existing_type=sa.String(length=200),
        existing_nullable=False,
        server_default="",
    )


def downgrade() -> None:
    op.alter_column(
        "deployments",
        "health_path",
        existing_type=sa.String(length=200),
        existing_nullable=False,
        server_default="/livez",
    )
