"""Мероприятия и розыгрыши.

Revision ID: 0010_events
Revises: 0009_party_finder
"""
import sqlalchemy as sa
from alembic import op

revision = "0010_events"
down_revision = "0009_party_finder"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "community_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("guild_id", sa.BigInteger, nullable=False),
        sa.Column("creator_id", sa.BigInteger, nullable=False),
        sa.Column("host_id", sa.BigInteger, nullable=False),
        sa.Column("channel_id", sa.BigInteger, nullable=False),
        sa.Column("message_id", sa.BigInteger),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("description", sa.String(800), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("starts_at", sa.BigInteger, nullable=False),
        sa.Column("ends_at", sa.BigInteger, nullable=False),
        sa.Column("registration_ends_at", sa.BigInteger, nullable=False),
        sa.Column("capacity", sa.Integer, nullable=False),
        sa.Column("waitlist_enabled", sa.Boolean, nullable=False),
        sa.Column("confirmed_count", sa.Integer, nullable=False),
        sa.Column("waitlist_count", sa.Integer, nullable=False),
        sa.Column("reminders", sa.String(100), nullable=False),
        sa.Column("reward_xp", sa.Integer, nullable=False),
        sa.Column("reward_coins", sa.Integer, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.BigInteger, nullable=False),
        sa.Column("updated_at", sa.BigInteger, nullable=False),
        sa.CheckConstraint("capacity BETWEEN 0 AND 1000", name="ck_event_capacity"),
        sa.CheckConstraint("confirmed_count >= 0 AND waitlist_count >= 0", name="ck_event_counts"),
        sa.CheckConstraint("status IN ('scheduled','live','finished','cancelled')", name="ck_event_status"),
        sa.CheckConstraint("registration_ends_at <= starts_at AND starts_at < ends_at", name="ck_event_times"),
    )
    op.create_index("ix_event_guild_status", "community_events", ["guild_id", "status", "starts_at"])
    op.create_table(
        "community_event_signups",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("event_id", sa.Integer, sa.ForeignKey("community_events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.BigInteger, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("registered_at", sa.BigInteger, nullable=False),
        sa.Column("attended_at", sa.BigInteger),
        sa.Column("confirmed_by", sa.BigInteger),
        sa.UniqueConstraint("event_id", "user_id", name="uq_event_signup"),
    )
    op.create_table(
        "community_event_reminders",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("event_id", sa.Integer, sa.ForeignKey("community_events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("minutes_before", sa.Integer, nullable=False),
        sa.Column("sent_at", sa.BigInteger),
        sa.UniqueConstraint("event_id", "minutes_before", name="uq_event_reminder"),
    )
    op.create_table(
        "community_giveaways",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("guild_id", sa.BigInteger, nullable=False),
        sa.Column("creator_id", sa.BigInteger, nullable=False),
        sa.Column("channel_id", sa.BigInteger, nullable=False),
        sa.Column("message_id", sa.BigInteger),
        sa.Column("prize", sa.String(200), nullable=False),
        sa.Column("conditions", sa.String(500), nullable=False),
        sa.Column("ends_at", sa.BigInteger, nullable=False),
        sa.Column("winner_count", sa.Integer, nullable=False),
        sa.Column("required_role_id", sa.BigInteger),
        sa.Column("min_level", sa.Integer, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("entry_count", sa.Integer, nullable=False),
        sa.Column("winners_json", sa.Text, nullable=False),
        sa.Column("draw_round", sa.Integer, nullable=False),
        sa.Column("created_at", sa.BigInteger, nullable=False),
        sa.Column("finished_at", sa.BigInteger),
        sa.CheckConstraint("winner_count BETWEEN 1 AND 20", name="ck_giveaway_winners"),
        sa.CheckConstraint("min_level BETWEEN 0 AND 1000", name="ck_giveaway_min_level"),
        sa.CheckConstraint("status IN ('open','ended','cancelled')", name="ck_giveaway_status"),
    )
    op.create_index("ix_giveaway_guild_status", "community_giveaways", ["guild_id", "status", "ends_at"])
    op.create_table(
        "community_giveaway_entries",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("giveaway_id", sa.Integer, sa.ForeignKey("community_giveaways.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.BigInteger, nullable=False),
        sa.Column("joined_at", sa.BigInteger, nullable=False),
        sa.UniqueConstraint("giveaway_id", "user_id", name="uq_giveaway_entry"),
    )
    op.create_table(
        "community_giveaway_draws",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("giveaway_id", sa.Integer, sa.ForeignKey("community_giveaways.id", ondelete="CASCADE"), nullable=False),
        sa.Column("round", sa.Integer, nullable=False),
        sa.Column("actor_id", sa.BigInteger, nullable=False),
        sa.Column("reason", sa.String(400), nullable=False),
        sa.Column("eligible_json", sa.Text, nullable=False),
        sa.Column("winners_json", sa.Text, nullable=False),
        sa.Column("algorithm", sa.String(48), nullable=False),
        sa.Column("created_at", sa.BigInteger, nullable=False),
        sa.UniqueConstraint("giveaway_id", "round", name="uq_giveaway_draw_round"),
    )


def downgrade():
    op.drop_table("community_giveaway_draws")
    op.drop_table("community_giveaway_entries")
    op.drop_index("ix_giveaway_guild_status", table_name="community_giveaways")
    op.drop_table("community_giveaways")
    op.drop_table("community_event_reminders")
    op.drop_table("community_event_signups")
    op.drop_index("ix_event_guild_status", table_name="community_events")
    op.drop_table("community_events")
