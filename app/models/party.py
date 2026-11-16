"""Объявления поиска команды и состав участников."""
from sqlalchemy import BigInteger, Boolean, CheckConstraint, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PartyListing(Base):
    __tablename__ = "party_listings"
    __table_args__ = (
        CheckConstraint("capacity BETWEEN 2 AND 25", name="ck_party_capacity"),
        CheckConstraint("member_count >= 1 AND member_count <= capacity", name="ck_party_count"),
        CheckConstraint("status IN ('open','full','closed','expired')", name="ck_party_status"),
        Index("ix_party_guild_status", "guild_id", "status", "expires_at"),
        Index("ix_party_owner", "guild_id", "owner_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    game: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    member_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    starts_at: Mapped[int | None] = mapped_column(BigInteger)
    expires_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    channel_id: Mapped[int | None] = mapped_column(BigInteger)
    message_id: Mapped[int | None] = mapped_column(BigInteger)
    voice_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    voice_channel_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class PartyMember(Base):
    __tablename__ = "party_members"
    __table_args__ = (UniqueConstraint("party_id", "user_id", name="uq_party_member"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    party_id: Mapped[int] = mapped_column(Integer, ForeignKey("party_listings.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    joined_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
