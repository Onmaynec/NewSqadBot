"""Уровни, защита от повторного XP, профили участников.

Revision ID: 0005_progression_profiles
Revises: 0004_tickets
"""
import sqlalchemy as sa
from alembic import op

revision = "0005_progression_profiles"
down_revision = "0004_tickets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "member_progress",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("guild_id", sa.BigInteger, nullable=False),
        sa.Column("user_id", sa.BigInteger, nullable=False),
        sa.Column("xp", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("level", sa.Integer, nullable=False, server_default="1"),
        sa.Column("credited_messages", sa.Integer, nullable=False, server_default="0"),
        sa.Column("active_voice_minutes", sa.Integer, nullable=False, server_default="0"),
        sa.Column("voice_bank_minutes", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_message_at", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("last_message_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("last_voice_minute", sa.BigInteger, nullable=False, server_default="-1"),
        sa.Column("daily_key", sa.String(10), nullable=False, server_default=""),
        sa.Column("daily_xp", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.BigInteger, nullable=False),
        sa.Column("updated_at", sa.BigInteger, nullable=False),
        sa.UniqueConstraint("guild_id", "user_id", name="uq_member_progress"),
        sa.CheckConstraint("xp >= 0 AND level >= 1 AND daily_xp >= 0", name="ck_progress_nonnegative"),
    )
    op.create_index("ix_member_progress_top", "member_progress", ["guild_id", "xp"])
    op.create_table(
        "experience_awards",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("guild_id", sa.BigInteger, nullable=False),
        sa.Column("user_id", sa.BigInteger, nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("reference", sa.String(100), nullable=False),
        sa.Column("amount", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.BigInteger, nullable=False),
        sa.UniqueConstraint("guild_id", "user_id", "source", "reference", name="uq_xp_award_reference"),
        sa.CheckConstraint("amount >= 0", name="ck_xp_award_amount"),
    )
    op.create_index("ix_xp_awards_user", "experience_awards", ["guild_id", "user_id", "created_at"])
    op.create_table(
        "member_profiles",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("guild_id", sa.BigInteger, nullable=False),
        sa.Column("user_id", sa.BigInteger, nullable=False),
        sa.Column("bio", sa.Text, nullable=False, server_default=""),
        sa.Column("games", sa.String(250), nullable=False, server_default=""),
        sa.Column("interests", sa.String(250), nullable=False, server_default=""),
        sa.Column("availability", sa.String(16), nullable=False, server_default="available"),
        sa.Column("public", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("updated_at", sa.BigInteger, nullable=False),
        sa.UniqueConstraint("guild_id", "user_id", name="uq_member_profiles"),
    )


def downgrade() -> None:
    op.drop_table("member_profiles")
    op.drop_index("ix_xp_awards_user", table_name="experience_awards")
    op.drop_table("experience_awards")
    op.drop_index("ix_member_progress_top", table_name="member_progress")
    op.drop_table("member_progress")
