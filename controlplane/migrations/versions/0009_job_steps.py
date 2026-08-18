"""Job steps table for pipeline graph progress tracking.

Creates job_steps table with columns per job_step model + FK CASCADE + unique
constraint + index. Each job can have multiple sequential steps (1-based index)
that represent stages in a deployment/provision pipeline.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_steps",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "job_id",
            sa.Uuid(),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("index", sa.Integer(), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="queued",
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("detail", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_unique_constraint(
        "uq_job_steps_job_index", "job_steps", ["job_id", "index"]
    )
    op.create_index("ix_job_steps_job_id", "job_steps", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_job_steps_job_id", table_name="job_steps")
    op.drop_constraint("uq_job_steps_job_index", "job_steps", type_="unique")
    op.drop_table("job_steps")