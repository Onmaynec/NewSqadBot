"""Предложения участников NewSquad.

Revision ID: 0011_suggestions
Revises: 0010_events
"""
import sqlalchemy as sa
from alembic import op

revision = "0011_suggestions"
down_revision = "0010_events"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "community_suggestions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("guild_id", sa.BigInteger, nullable=False),
        sa.Column("author_id", sa.BigInteger, nullable=False),
        sa.Column("channel_id", sa.BigInteger, nullable=False),
        sa.Column("message_id", sa.BigInteger),
        sa.Column("thread_id", sa.BigInteger),
        sa.Column("topic", sa.String(100), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("status_reason", sa.String(500), nullable=False),
        sa.Column("status_actor_id", sa.BigInteger),
        sa.Column("votes_for", sa.Integer, nullable=False),
        sa.Column("votes_against", sa.Integer, nullable=False),
        sa.Column("panel_missing", sa.Boolean, nullable=False),
        sa.Column("created_at", sa.BigInteger, nullable=False),
        sa.Column("updated_at", sa.BigInteger, nullable=False),
        sa.CheckConstraint(
            "status IN ('new','under_review','accepted','rejected','implemented')",
            name="ck_suggestion_status",
        ),
        sa.CheckConstraint("votes_for >= 0 AND votes_against >= 0", name="ck_suggestion_votes"),
    )
    op.create_index("ix_suggestion_guild_status", "community_suggestions",
                    ["guild_id", "status", "created_at"])
    op.create_index("ix_suggestion_author", "community_suggestions",
                    ["guild_id", "author_id", "created_at"])
    op.create_index("ix_suggestion_message", "community_suggestions",
                    ["guild_id", "channel_id", "message_id"])

    op.create_table(
        "community_suggestion_votes",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("suggestion_id", sa.Integer, sa.ForeignKey(
            "community_suggestions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.BigInteger, nullable=False),
        sa.Column("choice", sa.Integer, nullable=False),
        sa.Column("updated_at", sa.BigInteger, nullable=False),
        sa.UniqueConstraint("suggestion_id", "user_id", name="uq_suggestion_vote_user"),
        sa.CheckConstraint("choice IN (-1,1)", name="ck_suggestion_vote_choice"),
    )
    op.create_table(
        "community_suggestion_history",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("suggestion_id", sa.Integer, sa.ForeignKey(
            "community_suggestions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_id", sa.BigInteger, nullable=False),
        sa.Column("old_status", sa.String(20)),
        sa.Column("new_status", sa.String(20), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("created_at", sa.BigInteger, nullable=False),
    )
    op.create_index("ix_suggestion_history", "community_suggestion_history",
                    ["suggestion_id", "created_at"])
    op.create_table(
        "community_suggestion_throttle",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("guild_id", sa.BigInteger, nullable=False),
        sa.Column("user_id", sa.BigInteger, nullable=False),
        sa.Column("last_created_at", sa.BigInteger, nullable=False),
        sa.UniqueConstraint("guild_id", "user_id", name="uq_suggestion_throttle"),
    )
    op.create_table(
        "community_suggestion_panels",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("guild_id", sa.BigInteger, nullable=False),
        sa.Column("channel_id", sa.BigInteger, nullable=False),
        sa.Column("message_id", sa.BigInteger, nullable=False),
        sa.Column("missing", sa.Boolean, nullable=False),
        sa.Column("created_at", sa.BigInteger, nullable=False),
        sa.UniqueConstraint("guild_id", "message_id", name="uq_suggestion_panel_message"),
    )


def downgrade():
    op.drop_table("community_suggestion_panels")
    op.drop_table("community_suggestion_throttle")
    op.drop_index("ix_suggestion_history", table_name="community_suggestion_history")
    op.drop_table("community_suggestion_history")
    op.drop_table("community_suggestion_votes")
    op.drop_index("ix_suggestion_message", table_name="community_suggestions")
    op.drop_index("ix_suggestion_author", table_name="community_suggestions")
    op.drop_index("ix_suggestion_guild_status", table_name="community_suggestions")
    op.drop_table("community_suggestions")
