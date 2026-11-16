"""Временные голосовые комнаты и адресные разрешения доступа."""
from sqlalchemy import BigInteger, Boolean, CheckConstraint, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class VoiceRoom(Base):
    __tablename__ = "voice_rooms"
    __table_args__ = (
        UniqueConstraint("guild_id", "channel_id", name="uq_voice_room_channel"),
        UniqueConstraint("guild_id", "owner_id", name="uq_voice_room_owner"),
        CheckConstraint("user_limit >= 0 AND user_limit <= 99", name="ck_voice_room_limit"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    lobby_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    user_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    hidden: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class VoiceRoomAccess(Base):
    __tablename__ = "voice_room_access"
    __table_args__ = (
        UniqueConstraint("room_id", "user_id", name="uq_voice_room_access_user"),
        CheckConstraint("mode IN ('allow','deny')", name="ck_voice_room_access_mode"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    room_id: Mapped[int] = mapped_column(Integer, ForeignKey("voice_rooms.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mode: Mapped[str] = mapped_column(String(8), nullable=False)
