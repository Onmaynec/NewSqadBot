"""Приватные обращения в поддержку.

Revision ID: 0004_tickets
Revises: 0003_welcome_roles
"""
import sqlalchemy as sa
from alembic import op

revision = "0004_tickets"
down_revision = "0003_welcome_roles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("ticket_categories",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("guild_id", sa.BigInteger, nullable=False),
        sa.Column("key", sa.String(40), nullable=False),
        sa.Column("title", sa.String(90), nullable=False),
        sa.Column("description", sa.String(300), nullable=False),
        sa.Column("support_role_id", sa.BigInteger),
        sa.Column("candidate_allowed", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.UniqueConstraint("guild_id", "key", name="uq_ticket_category_key"),
    )
    op.create_table("ticket_panels",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("guild_id", sa.BigInteger, nullable=False),
        sa.Column("channel_id", sa.BigInteger, nullable=False),
        sa.Column("message_id", sa.BigInteger),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.BigInteger, nullable=False),
        sa.Column("created_at", sa.BigInteger, nullable=False),
    )
    op.create_table("tickets",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("guild_id", sa.BigInteger, nullable=False),
        sa.Column("user_id", sa.BigInteger, nullable=False),
        sa.Column("category_id", sa.Integer, sa.ForeignKey("ticket_categories.id"), nullable=False),
        sa.Column("channel_id", sa.BigInteger, unique=True),
        sa.Column("control_message_id", sa.BigInteger),
        sa.Column("staff_role_id", sa.BigInteger, nullable=False),
        sa.Column("subject", sa.String(100), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="creating"),
        sa.Column("assignee_id", sa.BigInteger),
        sa.Column("opened_at", sa.BigInteger, nullable=False),
        sa.Column("updated_at", sa.BigInteger, nullable=False),
        sa.Column("first_response_at", sa.BigInteger),
        sa.Column("closed_at", sa.BigInteger),
        sa.Column("closed_by", sa.BigInteger),
        sa.Column("close_reason", sa.String(500)),
        sa.Column("last_error", sa.String(500)),
        sa.Column("channel_deleted", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.CheckConstraint("status IN ('creating','open','closing','closed','failed')", name="ck_ticket_status"),
    )
    op.create_index("uq_ticket_category_active", "tickets", ["guild_id", "user_id", "category_id"],
                    unique=True, sqlite_where=sa.text("status IN ('creating','open','closing')"))
    op.create_index("ix_tickets_guild_status", "tickets", ["guild_id", "status"])
    op.create_table("ticket_participants",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("ticket_id", sa.Integer, sa.ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.BigInteger, nullable=False),
        sa.Column("added_by", sa.BigInteger, nullable=False),
        sa.Column("added_at", sa.BigInteger, nullable=False),
        sa.UniqueConstraint("ticket_id", "user_id", name="uq_ticket_participant"),
    )
    op.create_table("ticket_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("ticket_id", sa.Integer, sa.ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_id", sa.BigInteger, nullable=False),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("details_json", sa.Text, nullable=False),
        sa.Column("created_at", sa.BigInteger, nullable=False),
    )
    op.create_index("ix_ticket_events_ticket", "ticket_events", ["ticket_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_ticket_events_ticket", table_name="ticket_events")
    op.drop_table("ticket_events")
    op.drop_table("ticket_participants")
    op.drop_index("ix_tickets_guild_status", table_name="tickets")
    op.drop_index("uq_ticket_category_active", table_name="tickets")
    op.drop_table("tickets")
    op.drop_table("ticket_panels")
    op.drop_table("ticket_categories")
