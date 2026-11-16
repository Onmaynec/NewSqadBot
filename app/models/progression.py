"""Серверный прогресс, учёт начислений и публичный профиль участника."""
from sqlalchemy import BigInteger, Boolean, CheckConstraint, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class MemberProgress(Base):
    __tablename__ = "member_progress"
    __table_args__ = (
        UniqueConstraint("guild_id", "user_id", name="uq_member_progress"),
        Index("ix_member_progress_top", "guild_id", "xp"),
        CheckConstraint("xp >= 0 AND level >= 1 AND daily_xp >= 0", name="ck_progress_nonnegative"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    xp: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    level: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    credited_messages: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active_voice_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    voice_bank_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_message_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    last_message_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    last_voice_minute: Mapped[int] = mapped_column(BigInteger, nullable=False, default=-1)
    daily_key: Mapped[str] = mapped_column(String(10), nullable=False, default="")
    daily_xp: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class ExperienceAward(Base):
    __tablename__ = "experience_awards"
    __table_args__ = (
        UniqueConstraint("guild_id", "user_id", "source", "reference", name="uq_xp_award_reference"),
        Index("ix_xp_awards_user", "guild_id", "user_id", "created_at"),
        CheckConstraint("amount >= 0", name="ck_xp_award_amount"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    reference: Mapped[str] = mapped_column(String(100), nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class MemberProfile(Base):
    __tablename__ = "member_profiles"
    __table_args__ = (UniqueConstraint("guild_id", "user_id", name="uq_member_profiles"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    bio: Mapped[str] = mapped_column(Text, nullable=False, default="")
    games: Mapped[str] = mapped_column(String(250), nullable=False, default="")
    interests: Mapped[str] = mapped_column(String(250), nullable=False, default="")
    availability: Mapped[str] = mapped_column(String(16), nullable=False, default="available")
    public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
