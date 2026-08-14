"""deployments.strategy for progressive delivery

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-11

Covers docs/TODO.md Task 5.2 (progressive delivery): each deployment can
opt into an Argo Rollouts strategy ("canary" or "bluegreen") instead of the
plain Deployment rollout. Existing rows keep the default, which behaves
exactly as before.
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "deployments",
        sa.Column(
            "strategy",
            sa.String(20),
            server_default="deployment",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("deployments", "strategy")