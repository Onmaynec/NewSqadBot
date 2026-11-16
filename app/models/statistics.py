"""Дневные агрегаты статистики NewSquad. Содержимое сообщений не сохраняем."""
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class StatsDay(Base):
    __tablename__ = "statistics_daily"
    __table_args__ = (
        UniqueConstraint("guild_id", "user_id", "day", name="uq_stats_day_user"),
        Index("ix_stats_day_guild", "guild_id", "day"),
        CheckConstraint("messages >= 0 AND voice_minutes >= 0", name="ck_stats_daily_nonnegative"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    day: Mapped[str] = mapped_column(String(10), nullable=False)
    messages: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    voice_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class StatsReceipt(Base):
    __tablename__ = "statistics_receipts"
    __table_args__ = (
        UniqueConstraint("guild_id", "user_id", "kind", "reference", name="uq_stats_receipt"),
        Index("ix_stats_receipt_created", "created_at"),
        CheckConstraint("kind IN ('message','voice')", name="ck_stats_receipt_kind"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    reference: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class StatsServerDay(Base):
    __tablename__ = "statistics_server_daily"
    __table_args__ = (
        UniqueConstraint("guild_id", "day", name="uq_stats_server_day"),
        CheckConstraint("joins >= 0 AND leaves >= 0", name="ck_stats_member_events"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    day: Mapped[str] = mapped_column(String(10), nullable=False)
    joins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    leaves: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class StatsPresence(Base):
    __tablename__ = "statistics_presence"
    __table_args__ = (
        UniqueConstraint("guild_id", "user_id", name="uq_stats_presence"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    present: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    changed_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class StatsPanel(Base):
    __tablename__ = "statistics_panels"
    __table_args__ = (
        UniqueConstraint("guild_id", "channel_id", "kind", name="uq_stats_panel_channel_kind"),
        CheckConstraint("kind IN ('server','activity')", name="ck_stats_panel_kind"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
