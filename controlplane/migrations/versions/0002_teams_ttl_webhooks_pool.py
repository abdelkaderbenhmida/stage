"""teams, project TTL, webhook subscriptions, warm pool

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-11

Covers docs/TODO.md Task 2.2 (TTL), 2.4 (webhooks), 2.5 (warm pool) and
3.1 (teams). Existing projects are backfilled into a personal team per user so
the ownership change does not orphan anything.
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---------------------------------------------------------------- teams
    op.create_table(
        "teams",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column("slug", sa.String(length=60), nullable=False, unique=True),
        sa.Column("description", sa.Text()),
        sa.Column("is_personal", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    op.create_table(
        "team_members",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("team_id", sa.Uuid(), sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(length=20), server_default="developer", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("team_id", "user_id", name="uq_team_members_team_user"),
    )
    op.create_index("ix_team_members_user_id", "team_members", ["user_id"])
    op.create_index("ix_team_members_team_id", "team_members", ["team_id"])

    # ------------------------------------------------------- project fields
    op.add_column("projects", sa.Column("team_id", sa.Uuid(), sa.ForeignKey("teams.id"), nullable=True))
    op.add_column("projects", sa.Column("ttl_hours", sa.Integer(), server_default="24", nullable=False))
    op.add_column("projects", sa.Column("expires_at", sa.DateTime(timezone=True)))
    op.add_column("projects", sa.Column("auto_destroy", sa.Boolean(), server_default=sa.text("true"), nullable=False))
    op.add_column(
        "projects",
        sa.Column("last_accessed_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.add_column("projects", sa.Column("expiry_warned", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.create_index("ix_projects_team_id", "projects", ["team_id"])
    # The reaper scans on exactly this combination every few minutes.
    op.create_index("ix_projects_reaper", "projects", ["auto_destroy", "status", "expires_at"])

    # Backfill: one personal team per existing user, then move their projects
    # onto it. Without this every pre-existing project would have a NULL
    # team_id and become invisible once isolation switches to team scope.
    op.execute(
        """
        INSERT INTO teams (name, slug, description, is_personal)
        SELECT
            split_part(u.email, '@', 1),
            'personal-' || replace(u.id::text, '-', ''),
            'Personal team, created automatically during migration 0002.',
            true
        FROM users u
        """
    )
    op.execute(
        """
        INSERT INTO team_members (team_id, user_id, role)
        SELECT t.id, u.id, 'admin'
        FROM users u
        JOIN teams t ON t.slug = 'personal-' || replace(u.id::text, '-', '')
        """
    )
    op.execute(
        """
        UPDATE projects p
        SET team_id = t.id
        FROM teams t
        WHERE t.slug = 'personal-' || replace(p.owner_id::text, '-', '')
        """
    )

    # ------------------------------------------------ webhook subscriptions
    op.create_table(
        "webhook_subscriptions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column(
            "deployment_id", sa.Uuid(), sa.ForeignKey("deployments.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("provider", sa.String(length=20), server_default="github", nullable=False),
        sa.Column("secret", sa.Text(), nullable=False),
        sa.Column("repo_url", sa.Text(), nullable=False),
        sa.Column("branch", sa.String(length=255), server_default="main", nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("pull_request_number", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_webhook_subscriptions_deployment_id", "webhook_subscriptions", ["deployment_id"])
    op.create_index("ix_webhook_subscriptions_repo_branch", "webhook_subscriptions", ["repo_url", "branch"])

    # ------------------------------------------------------------ warm pool
    op.create_table(
        "pooled_clusters",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("spec_hash", sa.String(length=64), nullable=False),
        sa.Column("workspace_path", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="warming", nullable=False),
        sa.Column(
            "claimed_by_project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="SET NULL")
        ),
        sa.Column("node_ips", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_pooled_clusters_spec_hash_status", "pooled_clusters", ["spec_hash", "status"])

    # ------------------------------------------------ deferred debt (§8.9)
    op.create_index("ix_deployments_project_id_status", "deployments", ["project_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_deployments_project_id_status", table_name="deployments")
    op.drop_index("ix_pooled_clusters_spec_hash_status", table_name="pooled_clusters")
    op.drop_table("pooled_clusters")
    op.drop_index("ix_webhook_subscriptions_repo_branch", table_name="webhook_subscriptions")
    op.drop_index("ix_webhook_subscriptions_deployment_id", table_name="webhook_subscriptions")
    op.drop_table("webhook_subscriptions")
    op.drop_index("ix_projects_reaper", table_name="projects")
    op.drop_index("ix_projects_team_id", table_name="projects")
    for column in (
        "expiry_warned",
        "last_accessed_at",
        "auto_destroy",
        "expires_at",
        "ttl_hours",
        "team_id",
    ):
        op.drop_column("projects", column)
    op.drop_index("ix_team_members_team_id", table_name="team_members")
    op.drop_index("ix_team_members_user_id", table_name="team_members")
    op.drop_table("team_members")
    op.drop_table("teams")
