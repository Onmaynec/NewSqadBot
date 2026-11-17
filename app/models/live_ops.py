"""Оценки качества поддержки и персональные уведомления веб-портала.

Никакой переписки, причины или содержания обращения не хранится.
"""
from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class SupportSurvey(Base):
    __tablename__ = "support_surveys"
    __table_args__ = (
        CheckConstraint("rating IS NULL OR rating BETWEEN 1 AND 5", name="ck_support_rating"),
        Index("ix_support_surveys_staff", "guild_id", "staff_id", "rated_at"),
        Index("ix_support_surveys_delivery", "guild_id", "notified_at", "expires_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Нужен только до отправки оценки; после голосования обнуляется.
    requester_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    staff_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    response_seconds: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expires_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    notified_at: Mapped[int | None] = mapped_column(BigInteger)
    rating: Mapped[int | None] = mapped_column(Integer)
    rated_at: Mapped[int | None] = mapped_column(BigInteger)


class WebNotice(Base):
    __tablename__ = "web_notices"
    __table_args__ = (
        UniqueConstraint("guild_id", "dedupe_key", name="uq_web_notice_source"),
        Index("ix_web_notice_visibility", "guild_id", "created_at"),
        Index("ix_web_notice_target", "guild_id", "recipient_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(140), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    # support = всем сотрудникам, admin = только Admin/Owner.
    minimum_role: Mapped[str] = mapped_column(String(16), nullable=False)
    recipient_id: Mapped[int | None] = mapped_column(BigInteger)
    object_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class WebNoticeRead(Base):
    __tablename__ = "web_notice_reads"
    __table_args__ = (UniqueConstraint("notice_id", "user_id", name="uq_web_notice_read"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    notice_id: Mapped[int] = mapped_column(Integer, ForeignKey("web_notices.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    read_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
