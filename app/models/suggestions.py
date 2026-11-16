"""Идеи NewSquad: предложения, голоса, история решений и кнопки."""
from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, ForeignKey, Index, Integer,
    String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Suggestion(Base):
    __tablename__ = "community_suggestions"
    __table_args__ = (
        Index("ix_suggestion_guild_status", "guild_id", "status", "created_at"),
        Index("ix_suggestion_author", "guild_id", "author_id", "created_at"),
        Index("ix_suggestion_message", "guild_id", "channel_id", "message_id"),
        CheckConstraint(
            "status IN ('new','under_review','accepted','rejected','implemented')",
            name="ck_suggestion_status",
        ),
        CheckConstraint("votes_for >= 0 AND votes_against >= 0", name="ck_suggestion_votes"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    author_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[int | None] = mapped_column(BigInteger)
    thread_id: Mapped[int | None] = mapped_column(BigInteger)
    topic: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="new")
    status_reason: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    status_actor_id: Mapped[int | None] = mapped_column(BigInteger)
    votes_for: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    votes_against: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    panel_missing: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class SuggestionVote(Base):
    __tablename__ = "community_suggestion_votes"
    __table_args__ = (
        UniqueConstraint("suggestion_id", "user_id", name="uq_suggestion_vote_user"),
        CheckConstraint("choice IN (-1,1)", name="ck_suggestion_vote_choice"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    suggestion_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("community_suggestions.id", ondelete="CASCADE"), nullable=False,
    )
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    choice: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class SuggestionStatusEvent(Base):
    __tablename__ = "community_suggestion_history"
    __table_args__ = (
        Index("ix_suggestion_history", "suggestion_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    suggestion_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("community_suggestions.id", ondelete="CASCADE"), nullable=False,
    )
    actor_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    old_status: Mapped[str | None] = mapped_column(String(20))
    new_status: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class SuggestionThrottle(Base):
    __tablename__ = "community_suggestion_throttle"
    __table_args__ = (UniqueConstraint("guild_id", "user_id", name="uq_suggestion_throttle"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    last_created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class SuggestionCreatePanel(Base):
    __tablename__ = "community_suggestion_panels"
    __table_args__ = (UniqueConstraint("guild_id", "message_id", name="uq_suggestion_panel_message"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    missing: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
