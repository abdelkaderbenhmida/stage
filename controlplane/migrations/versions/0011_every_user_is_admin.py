"""Every user is admin — the operator-console role boundary is removed.

Explicit product decision: no more distinction between a regular tenant
account and a platform operator. `api/rbac.py:require_platform_admin` still
reads `users.role`; it just never sees anything but "admin" going forward.
Existing accounts are backfilled so this applies retroactively, not only to
new registrations — the whole point was "no difference", not "no difference
from here on".

Revision ID: 0011
Revises: 0010
"""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE users SET role = 'admin' WHERE role != 'admin'")
    op.alter_column(
        "users",
        "role",
        existing_type=sa.String(length=16),
        existing_nullable=False,
        server_default="admin",
    )


def downgrade() -> None:
    op.alter_column(
        "users",
        "role",
        existing_type=sa.String(length=16),
        existing_nullable=False,
        server_default="user",
    )
    # Not reversing the backfill: which accounts were "user" before upgrade()
    # ran is not recoverable, so downgrade restores the schema's shape, not
    # the data.
