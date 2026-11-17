"""Черновики, планирование и шаблоны объявлений.

Revision ID: 0013_announcements
Revises: 0012_statistics
"""
import sqlalchemy as sa
from alembic import op

revision = "0013_announcements"
down_revision = "0012_statistics"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "announcements",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("guild_id", sa.BigInteger, nullable=False),
        sa.Column("author_id", sa.BigInteger, nullable=False),
        sa.Column("channel_id", sa.BigInteger, nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("color", sa.Integer, nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("due_at", sa.BigInteger),
        sa.Column("message_id", sa.BigInteger),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error", sa.String(300)),
        sa.Column("created_at", sa.BigInteger, nullable=False),
        sa.Column("updated_at", sa.BigInteger, nullable=False),
        sa.CheckConstraint(
            "status IN ('draft','scheduled','sending','sent','uncertain','failed','cancelled')",
            name="ck_announcement_status",
        ),
        sa.CheckConstraint("kind IN ('text','embed')", name="ck_announcement_kind"),
    )
    op.create_index("ix_announcement_guild_status", "announcements", ["guild_id", "status", "due_at"])
    op.create_table(
        "announcement_templates",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("guild_id", sa.BigInteger, nullable=False),
        sa.Column("owner_id", sa.BigInteger, nullable=False),
        sa.Column("slug", sa.String(32), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("color", sa.Integer, nullable=False),
        sa.Column("updated_at", sa.BigInteger, nullable=False),
        sa.UniqueConstraint("guild_id", "slug", name="uq_announcement_template"),
    )


def downgrade():
    op.drop_table("announcement_templates")
    op.drop_index("ix_announcement_guild_status", table_name="announcements")
    op.drop_table("announcements")
