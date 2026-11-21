"""Member journey, support priority and voice owner preferences.

Revision ID: 0017_member_support_voice
Revises: 0016_staff_command_center
"""
import sqlalchemy as sa
from alembic import op

revision = "0017_member_support_voice"
down_revision = "0016_staff_command_center"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("membership_applications", sa.Column(
        "form_key", sa.String(32), nullable=False, server_default="standard"))
    op.add_column("membership_applications", sa.Column("extra_answer", sa.String(240)))
    op.add_column("membership_applications", sa.Column("followup_answer", sa.String(900)))
    op.add_column("membership_applications", sa.Column("stage_changed_at", sa.BigInteger()))
    op.add_column("tickets", sa.Column(
        "priority", sa.String(10), nullable=False, server_default="normal"))
    op.create_index("ix_tickets_priority", "tickets", ["guild_id", "priority", "status"])
    op.create_table(
        "membership_form_types",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("key", sa.String(32), nullable=False),
        sa.Column("title", sa.String(80), nullable=False),
        sa.Column("question", sa.String(100), nullable=False, server_default=""),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("guild_id", "key", name="uq_membership_form_key"),
    )
    op.create_table(
        "membership_checklist_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("application_id", sa.Integer(), sa.ForeignKey(
            "membership_applications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("item_key", sa.String(32), nullable=False),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("completed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("actor_id", sa.BigInteger()),
        sa.Column("changed_at", sa.BigInteger()),
        sa.UniqueConstraint("application_id", "item_key", name="uq_membership_checklist_item"),
    )
    op.create_index("ix_membership_checklist_app", "membership_checklist_items", ["application_id"])
    op.create_table(
        "voice_owner_preferences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("template", sa.String(16), nullable=False, server_default="public"),
        sa.Column("room_name", sa.String(90), nullable=False, server_default=""),
        sa.Column("user_limit", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("hidden", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("guild_id", "owner_id", name="uq_voice_owner_preference"),
    )
    op.create_table(
        "voice_usage_days",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("day_utc", sa.String(10), nullable=False),
        sa.Column("rooms_created", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("occupied_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("guild_id", "day_utc", name="uq_voice_usage_day"),
    )


def downgrade():
    op.drop_table("voice_usage_days")
    op.drop_table("voice_owner_preferences")
    op.drop_index("ix_membership_checklist_app", table_name="membership_checklist_items")
    op.drop_table("membership_checklist_items")
    op.drop_table("membership_form_types")
    op.drop_index("ix_tickets_priority", table_name="tickets")
    op.drop_column("tickets", "priority")
    op.drop_column("membership_applications", "stage_changed_at")
    op.drop_column("membership_applications", "followup_answer")
    op.drop_column("membership_applications", "extra_answer")
    op.drop_column("membership_applications", "form_key")
