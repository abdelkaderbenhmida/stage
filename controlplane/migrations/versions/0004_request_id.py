"""jobs.request_id for API -> worker correlation

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-12

docs/TODO.md §7 (request correlation): the API captures the incoming
X-Request-Id when a job is queued and stores it on the job row, so a
Celery worker mark the job's log lines with the originating request
without the serialised task needing to carry it.
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("request_id", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "request_id")