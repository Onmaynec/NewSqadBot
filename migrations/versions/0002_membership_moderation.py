"""Member applications and moderation history.

Revision ID: 0002_membership_moderation
Revises: 0001_core
"""
import sqlalchemy as sa
from alembic import op

revision = "0002_membership_moderation"
down_revision = "0001_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("membership_applications",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("guild_id", sa.BigInteger, nullable=False),
        sa.Column("user_id", sa.BigInteger, nullable=False),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("name", sa.String(24), nullable=False),
        sa.Column("gender", sa.String(8), nullable=False),
        sa.Column("age", sa.Integer, nullable=False),
        sa.Column("inviter", sa.String(120), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("decision", sa.String(24)),
        sa.Column("reviewer_id", sa.BigInteger),
        sa.Column("decision_reason", sa.Text),
        sa.Column("created_at", sa.BigInteger, nullable=False),
        sa.Column("decided_at", sa.BigInteger),
        sa.Column("review_channel_id", sa.BigInteger),
        sa.Column("review_message_id", sa.BigInteger),
        sa.Column("dm_status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("role_status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("female_role_status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("nickname_status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("kick_status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("last_error", sa.Text),
        sa.CheckConstraint("age BETWEEN 1 AND 120", name="ck_membership_age"),
    )
    op.create_index("ix_membership_user", "membership_applications", ["guild_id", "user_id", "created_at"])
    op.create_index("uq_membership_active", "membership_applications", ["guild_id", "user_id"], unique=True,
                    sqlite_where=sa.text("status IN ('pending','in_review','needs_info','approving','rejecting','action_failed')"))
    op.create_table("membership_history",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("application_id", sa.Integer, sa.ForeignKey("membership_applications.id"), nullable=False),
        sa.Column("actor_id", sa.BigInteger, nullable=False),
        sa.Column("old_status", sa.String(24), nullable=False),
        sa.Column("new_status", sa.String(24), nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("created_at", sa.BigInteger, nullable=False),
    )
    op.create_table("moderation_cases",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("guild_id", sa.BigInteger, nullable=False),
        sa.Column("user_id", sa.BigInteger, nullable=False),
        sa.Column("moderator_id", sa.BigInteger, nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.BigInteger, nullable=False),
        sa.Column("expires_at", sa.BigInteger),
        sa.Column("completed_at", sa.BigInteger),
        sa.Column("closed_by", sa.BigInteger),
        sa.Column("dm_status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("error", sa.Text),
    )
    op.create_index("ix_moderation_target", "moderation_cases", ["guild_id", "user_id", "created_at"])
    op.create_index("uq_active_managed_ban", "moderation_cases", ["guild_id", "user_id"], unique=True,
                    sqlite_where=sa.text("kind = 'ban' AND status IN ('pending','active')"))
    op.create_table("moderation_actions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("case_id", sa.Integer, nullable=False),
        sa.Column("actor_id", sa.BigInteger, nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("details", sa.Text, nullable=False),
        sa.Column("created_at", sa.BigInteger, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("moderation_actions")
    op.drop_index("uq_active_managed_ban", table_name="moderation_cases")
    op.drop_index("ix_moderation_target", table_name="moderation_cases")
    op.drop_table("moderation_cases")
    op.drop_table("membership_history")
    op.drop_index("uq_membership_active", table_name="membership_applications")
    op.drop_index("ix_membership_user", table_name="membership_applications")
    op.drop_table("membership_applications")
