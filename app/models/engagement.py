"""Квитанции наград: один пользователь получает награду один раз за период/достижение."""
from sqlalchemy import BigInteger, CheckConstraint, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class QuestClaim(Base):
    __tablename__ = "quest_claims"
    __table_args__ = (
        UniqueConstraint("guild_id", "user_id", "code", "period", name="uq_quest_claim_once"),
        Index("ix_quest_claims_user", "guild_id", "user_id", "claimed_at"),
        CheckConstraint("xp_reward >= 0 AND coin_reward >= 0", name="ck_quest_rewards_nonnegative"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    period: Mapped[str] = mapped_column(String(10), nullable=False)
    frequency: Mapped[str] = mapped_column(String(8), nullable=False)
    xp_reward: Mapped[int] = mapped_column(Integer, nullable=False)
    coin_reward: Mapped[int] = mapped_column(Integer, nullable=False)
    claimed_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class AchievementUnlock(Base):
    __tablename__ = "achievement_unlocks"
    __table_args__ = (
        UniqueConstraint("guild_id", "user_id", "code", name="uq_achievement_once"),
        Index("ix_achievement_user", "guild_id", "user_id", "unlocked_at"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    unlocked_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    claimed_at: Mapped[int | None] = mapped_column(BigInteger)
