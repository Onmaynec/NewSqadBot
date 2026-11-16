"""Мероприятия, регистрация, напоминания и история розыгрышей NewSquad."""
from sqlalchemy import BigInteger, Boolean, CheckConstraint, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class CommunityEvent(Base):
    __tablename__ = "community_events"
    __table_args__ = (
        Index("ix_event_guild_status", "guild_id", "status", "starts_at"),
        CheckConstraint("capacity BETWEEN 0 AND 1000", name="ck_event_capacity"),
        CheckConstraint("confirmed_count >= 0 AND waitlist_count >= 0", name="ck_event_counts"),
        CheckConstraint("status IN ('scheduled','live','finished','cancelled')", name="ck_event_status"),
        CheckConstraint("registration_ends_at <= starts_at AND starts_at < ends_at", name="ck_event_times"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    creator_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    host_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[int | None] = mapped_column(BigInteger)
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(String(800), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    starts_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ends_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    registration_ends_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    waitlist_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    confirmed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    waitlist_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reminders: Mapped[str] = mapped_column(String(100), nullable=False, default="60,15")
    reward_xp: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reward_coins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="scheduled")
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class EventSignup(Base):
    __tablename__ = "community_event_signups"
    __table_args__ = (UniqueConstraint("event_id", "user_id", name="uq_event_signup"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(Integer, ForeignKey("community_events.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    registered_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    attended_at: Mapped[int | None] = mapped_column(BigInteger)
    confirmed_by: Mapped[int | None] = mapped_column(BigInteger)


class EventReminder(Base):
    __tablename__ = "community_event_reminders"
    __table_args__ = (UniqueConstraint("event_id", "minutes_before", name="uq_event_reminder"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(Integer, ForeignKey("community_events.id", ondelete="CASCADE"), nullable=False)
    minutes_before: Mapped[int] = mapped_column(Integer, nullable=False)
    sent_at: Mapped[int | None] = mapped_column(BigInteger)


class Giveaway(Base):
    __tablename__ = "community_giveaways"
    __table_args__ = (
        Index("ix_giveaway_guild_status", "guild_id", "status", "ends_at"),
        CheckConstraint("winner_count BETWEEN 1 AND 20", name="ck_giveaway_winners"),
        CheckConstraint("min_level BETWEEN 0 AND 1000", name="ck_giveaway_min_level"),
        CheckConstraint("status IN ('open','ended','cancelled')", name="ck_giveaway_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    creator_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[int | None] = mapped_column(BigInteger)
    prize: Mapped[str] = mapped_column(String(200), nullable=False)
    conditions: Mapped[str] = mapped_column(String(500), nullable=False)
    ends_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    winner_count: Mapped[int] = mapped_column(Integer, nullable=False)
    required_role_id: Mapped[int | None] = mapped_column(BigInteger)
    min_level: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    entry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    winners_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    draw_round: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    finished_at: Mapped[int | None] = mapped_column(BigInteger)


class GiveawayEntry(Base):
    __tablename__ = "community_giveaway_entries"
    __table_args__ = (UniqueConstraint("giveaway_id", "user_id", name="uq_giveaway_entry"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    giveaway_id: Mapped[int] = mapped_column(Integer, ForeignKey("community_giveaways.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    joined_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class GiveawayDraw(Base):
    __tablename__ = "community_giveaway_draws"
    __table_args__ = (UniqueConstraint("giveaway_id", "round", name="uq_giveaway_draw_round"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    giveaway_id: Mapped[int] = mapped_column(Integer, ForeignKey("community_giveaways.id", ondelete="CASCADE"), nullable=False)
    round: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reason: Mapped[str] = mapped_column(String(400), nullable=False)
    eligible_json: Mapped[str] = mapped_column(Text, nullable=False)
    winners_json: Mapped[str] = mapped_column(Text, nullable=False)
    algorithm: Mapped[str] = mapped_column(String(48), nullable=False)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
