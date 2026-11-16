"""Дневные показатели, счётчики членства и панели Statistics.

Revision ID: 0012_statistics
Revises: 0011_suggestions
"""
import sqlalchemy as sa
from alembic import op

revision = "0012_statistics"
down_revision = "0011_suggestions"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "statistics_daily",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("guild_id", sa.BigInteger, nullable=False),
        sa.Column("user_id", sa.BigInteger, nullable=False),
        sa.Column("day", sa.String(10), nullable=False),
        sa.Column("messages", sa.Integer, nullable=False, server_default="0"),
        sa.Column("voice_minutes", sa.Integer, nullable=False, server_default="0"),
        sa.UniqueConstraint("guild_id", "user_id", "day", name="uq_stats_day_user"),
        sa.CheckConstraint("messages >= 0 AND voice_minutes >= 0", name="ck_stats_daily_nonnegative"),
    )
    op.create_index("ix_stats_day_guild", "statistics_daily", ["guild_id", "day"])

    op.create_table(
        "statistics_receipts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("guild_id", sa.BigInteger, nullable=False),
        sa.Column("user_id", sa.BigInteger, nullable=False),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("reference", sa.String(40), nullable=False),
        sa.Column("created_at", sa.BigInteger, nullable=False),
        sa.UniqueConstraint("guild_id", "user_id", "kind", "reference", name="uq_stats_receipt"),
        sa.CheckConstraint("kind IN ('message','voice')", name="ck_stats_receipt_kind"),
    )
    op.create_index("ix_stats_receipt_created", "statistics_receipts", ["created_at"])

    op.create_table(
        "statistics_server_daily",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("guild_id", sa.BigInteger, nullable=False),
        sa.Column("day", sa.String(10), nullable=False),
        sa.Column("joins", sa.Integer, nullable=False, server_default="0"),
        sa.Column("leaves", sa.Integer, nullable=False, server_default="0"),
        sa.UniqueConstraint("guild_id", "day", name="uq_stats_server_day"),
        sa.CheckConstraint("joins >= 0 AND leaves >= 0", name="ck_stats_member_events"),
    )
    op.create_table(
        "statistics_presence",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("guild_id", sa.BigInteger, nullable=False),
        sa.Column("user_id", sa.BigInteger, nullable=False),
        sa.Column("present", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("changed_at", sa.BigInteger, nullable=False),
        sa.UniqueConstraint("guild_id", "user_id", name="uq_stats_presence"),
    )
    op.create_table(
        "statistics_panels",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("guild_id", sa.BigInteger, nullable=False),
        sa.Column("channel_id", sa.BigInteger, nullable=False),
        sa.Column("message_id", sa.BigInteger, nullable=False),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("updated_at", sa.BigInteger, nullable=False),
        sa.UniqueConstraint("guild_id", "channel_id", "kind", name="uq_stats_panel_channel_kind"),
        sa.CheckConstraint("kind IN ('server','activity')", name="ck_stats_panel_kind"),
    )


def downgrade():
    op.drop_table("statistics_panels")
    op.drop_table("statistics_presence")
    op.drop_table("statistics_server_daily")
    op.drop_index("ix_stats_receipt_created", table_name="statistics_receipts")
    op.drop_table("statistics_receipts")
    op.drop_index("ix_stats_day_guild", table_name="statistics_daily")
    op.drop_table("statistics_daily")
