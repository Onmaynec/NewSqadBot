"""Кошельки, журнал операций, товары и предметы NewSquad."""
from sqlalchemy import BigInteger, Boolean, CheckConstraint, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Wallet(Base):
    __tablename__ = "economy_wallets"
    __table_args__ = (
        UniqueConstraint("guild_id", "user_id", name="uq_economy_wallet"),
        CheckConstraint("balance >= 0 AND earned >= 0 AND spent >= 0", name="ck_economy_wallet_nonnegative"),
        Index("ix_economy_wallet_rank", "guild_id", "balance"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    balance: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    earned: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    spent: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    last_daily: Mapped[str] = mapped_column(String(10), nullable=False, default="")
    daily_streak: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class CurrencyEntry(Base):
    __tablename__ = "economy_ledger"
    __table_args__ = (
        UniqueConstraint("guild_id", "reference", name="uq_economy_reference"),
        Index("ix_economy_user_ledger", "guild_id", "user_id", "created_at"),
        CheckConstraint("amount != 0", name="ck_economy_ledger_nonzero"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    reference: Mapped[str] = mapped_column(String(180), nullable=False)
    actor_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class ShopItem(Base):
    __tablename__ = "economy_shop_items"
    __table_args__ = (
        UniqueConstraint("guild_id", "code", name="uq_economy_item_code"),
        CheckConstraint("price > 0 AND sold >= 0 AND (stock IS NULL OR stock >= 1)", name="ck_economy_stock"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    price: Mapped[int] = mapped_column(Integer, nullable=False)
    role_id: Mapped[int | None] = mapped_column(BigInteger)
    stock: Mapped[int | None] = mapped_column(Integer)
    sold: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class InventoryItem(Base):
    __tablename__ = "economy_inventory"
    __table_args__ = (
        UniqueConstraint("guild_id", "user_id", "item_id", name="uq_economy_ownership"),
        Index("ix_economy_inventory_user", "guild_id", "user_id"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    item_id: Mapped[int] = mapped_column(Integer, ForeignKey("economy_shop_items.id"), nullable=False)
    equipped: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # For Discord role goods: pending until the role has actually been granted.
    delivery: Mapped[str] = mapped_column(String(16), nullable=False, default="not_required")
    purchased_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
