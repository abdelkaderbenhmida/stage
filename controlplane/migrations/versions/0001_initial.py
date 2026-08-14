"""initial tables

Revision ID: 0001
Revises:
Create Date: 2026-01-01

Every table from docs/PLATFORM_SPEC.md §5 plus refresh_tokens and audit_log.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("email", sa.Text(), nullable=False, unique=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("role", sa.String(length=16), server_default="user", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    op.create_table(
        "projects",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(length=30), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("status", sa.String(length=20), server_default="draft", nullable=False),
        sa.Column("infra_spec", postgresql.JSONB(), nullable=False),
        sa.Column("workspace_path", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("owner_id", "name", name="uq_projects_owner_name"),
    )
    op.create_index("ix_projects_owner_id", "projects", ["owner_id"])

    op.create_table(
        "nodes",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=20), nullable=False),
        sa.Column("vcpu", sa.Integer(), nullable=False),
        sa.Column("memory_mb", sa.Integer(), nullable=False),
        sa.Column("disk_gb", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("ip_address", sa.String(length=45)),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_nodes_project_id", "nodes", ["project_id"])

    op.create_table(
        "deployments",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("service_name", sa.String(length=120), nullable=False),
        sa.Column("repo_url", sa.Text(), nullable=False),
        sa.Column("branch", sa.String(length=120), server_default="main", nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("image_ref", sa.Text()),
        sa.Column("status", sa.String(length=20), server_default="queued", nullable=False),
        sa.Column("live_url", sa.Text()),
        sa.Column("replicas", sa.Integer(), server_default="2", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_deployments_project_id", "deployments", ["project_id"])

    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.Text(), unique=True, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    op.create_table(
        "jobs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="SET NULL")),
        sa.Column("deployment_id", sa.Uuid(), sa.ForeignKey("deployments.id", ondelete="SET NULL")),
        sa.Column("type", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="queued", nullable=False),
        sa.Column("celery_task_id", sa.Text()),
        sa.Column("log", sa.Text(), server_default=""),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("error_message", sa.Text()),
        sa.Column("cancel_requested", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_jobs_project_id_status", "jobs", ["project_id", "status"])

    op.create_table(
        "scans",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("deployment_id", sa.Uuid(), sa.ForeignKey("deployments.id", ondelete="SET NULL")),
        sa.Column("tool", sa.String(length=20), nullable=False),
        sa.Column("target", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="queued", nullable=False),
        sa.Column("raw_output", postgresql.JSONB()),
        sa.Column("summary", postgresql.JSONB()),
        sa.Column("duration_seconds", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_scans_project_id_created_at", "scans", ["project_id", sa.text("created_at DESC")])

    op.create_table(
        "findings",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("scan_id", sa.Uuid(), sa.ForeignKey("scans.id", ondelete="CASCADE"), nullable=False),
        sa.Column("severity", sa.String(length=12), nullable=False),
        sa.Column("identifier", sa.Text()),
        sa.Column("package_name", sa.Text()),
        sa.Column("installed_version", sa.Text()),
        sa.Column("fixed_version", sa.Text()),
        sa.Column("title", sa.Text()),
        sa.Column("description", sa.Text()),
        sa.Column("file_path", sa.Text()),
        sa.Column("line_number", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_findings_scan_id_severity", "findings", ["scan_id", "severity"])

    op.create_table(
        "audit_log",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("action", sa.String(length=60), nullable=False),
        sa.Column("resource_type", sa.String(length=60)),
        sa.Column("resource_id", sa.Text()),
        sa.Column("ip_address", sa.String(length=45)),
        sa.Column("detail", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_audit_log_user_id", "audit_log", ["user_id"])
    op.create_index("ix_audit_log_created_at", "audit_log", ["created_at"])


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_table("findings")
    op.drop_table("scans")
    op.drop_table("jobs")
    op.drop_table("refresh_tokens")
    op.drop_table("deployments")
    op.drop_table("nodes")
    op.drop_table("projects")
    op.drop_table("users")
