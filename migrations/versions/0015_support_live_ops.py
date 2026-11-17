"""Support quality ratings and staff portal notifications (no ticket transcripts).

Revision ID: 0015_support_live_ops
Revises: 0014_community_categories
"""
import sqlalchemy as sa
from alembic import op

revision = "0015_support_live_ops"
down_revision = "0014_community_categories"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("support_surveys",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("requester_id", sa.BigInteger(), nullable=False),
        sa.Column("staff_id", sa.BigInteger(), nullable=False),
        sa.Column("response_seconds", sa.Integer()),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.BigInteger(), nullable=False),
        sa.Column("notified_at", sa.BigInteger()),
        sa.Column("rating", sa.Integer()),
        sa.Column("rated_at", sa.BigInteger()),
        sa.CheckConstraint("rating IS NULL OR rating BETWEEN 1 AND 5", name="ck_support_rating"))
    op.create_index("ix_support_surveys_staff", "support_surveys", ["guild_id", "staff_id", "rated_at"])
    op.create_index("ix_support_surveys_delivery", "support_surveys", ["guild_id", "notified_at", "expires_at"])
    op.create_table("web_notices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("dedupe_key", sa.String(140), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("minimum_role", sa.String(16), nullable=False),
        sa.Column("recipient_id", sa.BigInteger()),
        sa.Column("object_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("guild_id", "dedupe_key", name="uq_web_notice_source"))
    op.create_index("ix_web_notice_visibility", "web_notices", ["guild_id", "created_at"])
    op.create_index("ix_web_notice_target", "web_notices", ["guild_id", "recipient_id", "created_at"])
    op.create_table("web_notice_reads",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("notice_id", sa.Integer(), sa.ForeignKey("web_notices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("read_at", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("notice_id", "user_id", name="uq_web_notice_read"))


def downgrade():
    op.drop_table("web_notice_reads")
    op.drop_index("ix_web_notice_target", table_name="web_notices")
    op.drop_index("ix_web_notice_visibility", table_name="web_notices")
    op.drop_table("web_notices")
    op.drop_index("ix_support_surveys_delivery", table_name="support_surveys")
    op.drop_index("ix_support_surveys_staff", table_name="support_surveys")
    op.drop_table("support_surveys")
