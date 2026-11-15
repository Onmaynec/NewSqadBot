"""Initial core schema.

Revision ID: 0001_core
Revises:
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_core"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "guild_settings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("guild_id", sa.BigInteger, nullable=False),
        sa.Column("key", sa.String(120), nullable=False),
        sa.Column("value_json", sa.Text, nullable=False),
        sa.UniqueConstraint("guild_id", "key", name="uq_guild_settings_key"),
    )
    op.create_table(
        "system_audit",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("guild_id", sa.BigInteger, nullable=False),
        sa.Column("actor_id", sa.BigInteger, nullable=False),
        sa.Column("action", sa.String(120), nullable=False),
        sa.Column("data_json", sa.Text, nullable=False),
        sa.Column("created_at", sa.BigInteger, nullable=False),
    )
    op.create_index("ix_system_audit_guild_created", "system_audit", ["guild_id", "created_at"])
    op.create_table(
        "scheduled_jobs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("job_key", sa.String(180), nullable=False, unique=True),
        sa.Column("handler", sa.String(120), nullable=False),
        sa.Column("payload_json", sa.Text, nullable=False),
        sa.Column("due_at", sa.BigInteger, nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("locked_until", sa.BigInteger, nullable=False, server_default="0"),
    )
    op.create_index("ix_scheduled_jobs_due", "scheduled_jobs", ["status", "due_at"])
    op.create_table(
        "outbox_events",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("kind", sa.String(120), nullable=False),
        sa.Column("payload_json", sa.Text, nullable=False),
        sa.Column("created_at", sa.BigInteger, nullable=False),
    )
    op.create_table(
        "event_receipts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("event_id", sa.String(64), nullable=False),
        sa.Column("handler", sa.String(120), nullable=False),
        sa.Column("completed_at", sa.BigInteger, nullable=False),
        sa.UniqueConstraint("event_id", "handler", name="uq_event_handler"),
    )


def downgrade() -> None:
    op.drop_table("event_receipts")
    op.drop_table("outbox_events")
    op.drop_index("ix_scheduled_jobs_due", table_name="scheduled_jobs")
    op.drop_table("scheduled_jobs")
    op.drop_index("ix_system_audit_guild_created", table_name="system_audit")
    op.drop_table("system_audit")
    op.drop_table("guild_settings")
