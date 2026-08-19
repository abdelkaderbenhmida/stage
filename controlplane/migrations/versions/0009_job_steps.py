"""Job steps table for pipeline graph progress tracking.

Creates job_steps with per-step timestamps so the pipeline graph survives
log truncation (the log is capped head-first at 200 kB and the early
"[n/N]" markers vanish on long builds; a row does not).
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
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("step_total", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="running",
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("error_message", sa.Text()),
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
        "uq_job_steps_job_id_step_index", "job_steps", ["job_id", "step_index"]
    )
    op.create_index("ix_job_steps_job_id", "job_steps", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_job_steps_job_id", table_name="job_steps")
    op.drop_constraint(
        "uq_job_steps_job_id_step_index", "job_steps", type_="unique"
    )
    op.drop_table("job_steps")
