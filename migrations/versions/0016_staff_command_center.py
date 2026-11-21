"""Staff Command Center tasks, templates and idempotent operation receipts.

Revision ID: 0016_staff_command_center
Revises: 0015_support_live_ops
"""
import sqlalchemy as sa
from alembic import op

revision = "0016_staff_command_center"
down_revision = "0015_support_live_ops"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "staff_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("assigned_to", sa.BigInteger(), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("description", sa.String(600), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("due_at", sa.BigInteger()),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("status IN ('open','done')", name="ck_staff_task_status"),
    )
    op.create_index("ix_staff_tasks_assignee", "staff_tasks", ["guild_id", "assigned_to", "status"])
    op.create_table(
        "staff_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("body", sa.String(1500), nullable=False),
        sa.Column("updated_by", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("guild_id", "scope", "title", name="uq_staff_template"),
    )
    op.create_table(
        "staff_operations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("request_key", sa.String(72), nullable=False),
        sa.Column("target_kind", sa.String(20), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("actor_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("summary", sa.String(240), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("guild_id", "request_key", "target_kind", "target_id",
                            name="uq_staff_operation_idempotency"),
    )
    op.create_index("ix_staff_operations_history", "staff_operations", ["guild_id", "created_at"])


def downgrade():
    op.drop_index("ix_staff_operations_history", table_name="staff_operations")
    op.drop_table("staff_operations")
    op.drop_table("staff_templates")
    op.drop_index("ix_staff_tasks_assignee", table_name="staff_tasks")
    op.drop_table("staff_tasks")
