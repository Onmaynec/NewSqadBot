"""v2.1.0: compact membership workflow metadata and privacy-safe review checklist."""
from sqlalchemy import BigInteger, Boolean, CheckConstraint, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ApplicationFormType(Base):
    __tablename__ = "membership_form_types"
    __table_args__ = (
        UniqueConstraint("guild_id", "key", name="uq_membership_form_key"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    key: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(80), nullable=False)
    question: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class ApplicationChecklistItem(Base):
    __tablename__ = "membership_checklist_items"
    __table_args__ = (
        UniqueConstraint("application_id", "item_key", name="uq_membership_checklist_item"),
        Index("ix_membership_checklist_app", "application_id"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(Integer, ForeignKey("membership_applications.id",
                                                        ondelete="CASCADE"), nullable=False)
    item_key: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    actor_id: Mapped[int | None] = mapped_column(BigInteger)
    changed_at: Mapped[int | None] = mapped_column(BigInteger)


class VoiceOwnerPreference(Base):
    __tablename__ = "voice_owner_preferences"
    __table_args__ = (UniqueConstraint("guild_id", "owner_id", name="uq_voice_owner_preference"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    template: Mapped[str] = mapped_column(String(16), nullable=False, default="public")
    room_name: Mapped[str] = mapped_column(String(90), nullable=False, default="")
    user_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    hidden: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class VoiceUsageDay(Base):
    __tablename__ = "voice_usage_days"
    __table_args__ = (UniqueConstraint("guild_id", "day_utc", name="uq_voice_usage_day"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    day_utc: Mapped[str] = mapped_column(String(10), nullable=False)
    rooms_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    occupied_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
