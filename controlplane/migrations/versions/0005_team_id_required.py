"""team_id becomes the sole tenancy boundary (multi-tenancy plan Phase 1).

Migration 0002 already backfilled every existing project onto a personal
team, so by the time this runs no row should have a NULL team_id — this
migration's own backfill is a defensive safety net, not the primary path,
covering any project created between 0002 and now through a code path that
somehow skipped setting it.

Drops the owner_id-based uniqueness in favour of team-scoped naming: two
different teams may now each have a project named "staging" (they could not
before), matching the reality that owner_id has not been the access-control
boundary since 0002.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Defensive: catch any project a bug left without a team, by giving it
    # (or creating) the owner's personal team, same rule 0002 used.
    op.execute(
        """
        INSERT INTO teams (name, slug, description, is_personal)
        SELECT
            split_part(u.email, '@', 1),
            'personal-' || replace(u.id::text, '-', ''),
            'Personal team, created automatically during migration 0005.',
            true
        FROM users u
        JOIN projects p ON p.owner_id = u.id
        WHERE p.team_id IS NULL
        ON CONFLICT (slug) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO team_members (team_id, user_id, role)
        SELECT t.id, u.id, 'admin'
        FROM users u
        JOIN projects p ON p.owner_id = u.id
        JOIN teams t ON t.slug = 'personal-' || replace(u.id::text, '-', '')
        WHERE p.team_id IS NULL
        ON CONFLICT (team_id, user_id) DO NOTHING
        """
    )
    op.execute(
        """
        UPDATE projects p
        SET team_id = t.id
        FROM teams t
        WHERE p.team_id IS NULL
          AND t.slug = 'personal-' || replace(p.owner_id::text, '-', '')
        """
    )

    op.alter_column("projects", "team_id", existing_type=sa.Uuid(), nullable=False)

    op.drop_constraint("uq_projects_owner_name", "projects", type_="unique")
    op.create_unique_constraint("uq_projects_team_name", "projects", ["team_id", "name"])

    op.add_column(
        "audit_log", sa.Column("team_id", sa.Uuid(), sa.ForeignKey("teams.id", ondelete="SET NULL"))
    )
    op.create_index("ix_audit_log_team_id", "audit_log", ["team_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_log_team_id", table_name="audit_log")
    op.drop_column("audit_log", "team_id")

    op.drop_constraint("uq_projects_team_name", "projects", type_="unique")
    op.create_unique_constraint("uq_projects_owner_name", "projects", ["owner_id", "name"])

    op.alter_column("projects", "team_id", existing_type=sa.Uuid(), nullable=True)
