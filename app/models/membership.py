"""Заявки на вступление и журнал решений."""
from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Application(Base):
    __tablename__ = "membership_applications"
    __table_args__ = (
        Index("ix_membership_user", "guild_id", "user_id", "created_at"),
        Index("uq_membership_active", "guild_id", "user_id", unique=True,
              sqlite_where=text("status IN ('pending', 'in_review', 'needs_info', 'approving', 'rejecting', 'action_failed')")),
        CheckConstraint("age BETWEEN 1 AND 120", name="ck_membership_age"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(24), nullable=False)
    gender: Mapped[str] = mapped_column(String(8), nullable=False)
    age: Mapped[int] = mapped_column(Integer, nullable=False)
    inviter: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    decision: Mapped[str | None] = mapped_column(String(24))
    reviewer_id: Mapped[int | None] = mapped_column(BigInteger)
    decision_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    decided_at: Mapped[int | None] = mapped_column(BigInteger)
    review_channel_id: Mapped[int | None] = mapped_column(BigInteger)
    review_message_id: Mapped[int | None] = mapped_column(BigInteger)
    dm_status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    role_status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    female_role_status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    nickname_status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    kick_status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    candidate_role_status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)


class ApplicationEvent(Base):
    __tablename__ = "membership_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(Integer, ForeignKey("membership_applications.id"), nullable=False)
    actor_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    old_status: Mapped[str] = mapped_column(String(24), nullable=False)
    new_status: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
