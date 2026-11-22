"""Independent real-money order storefront; never mixes with NS Coins."""
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class StoreProduct(Base):
    __tablename__ = "store_products"
    __table_args__ = (
        UniqueConstraint("guild_id", "sku", name="uq_store_product_sku"),
        CheckConstraint("price_minor > 0 AND (stock IS NULL OR stock >= 0)",
                        name="ck_store_product_price_stock"),
        Index("ix_store_products_public", "guild_id", "active", "sort_order"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sku: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(90), nullable=False)
    description: Mapped[str] = mapped_column(String(600), nullable=False, default="")
    category: Mapped[str] = mapped_column(String(40), nullable=False, default="digital")
    price_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="RUB")
    stock: Mapped[int | None] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class StoreOrder(Base):
    __tablename__ = "store_orders"
    __table_args__ = (
        UniqueConstraint("guild_id", "interaction_id", name="uq_store_checkout_interaction"),
        CheckConstraint("status IN ('awaiting_payment','payment_reported','processing','completed','cancelled')",
                        name="ck_store_order_status"),
        CheckConstraint("quantity BETWEEN 1 AND 10 AND price_minor > 0",
                        name="ck_store_order_quantity"),
        Index("ix_store_orders_owner", "guild_id", "user_id", "created_at"),
        Index("ix_store_orders_queue", "guild_id", "status", "created_at"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    product_id: Mapped[int] = mapped_column(Integer, ForeignKey("store_products.id"), nullable=False)
    interaction_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sku: Mapped[str] = mapped_column(String(40), nullable=False)
    product_title: Mapped[str] = mapped_column(String(90), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    price_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="awaiting_payment")
    staff_message_id: Mapped[int | None] = mapped_column(BigInteger)
    staff_channel_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    completed_at: Mapped[int | None] = mapped_column(BigInteger)


class StoreOrderEvent(Base):
    __tablename__ = "store_order_events"
    __table_args__ = (Index("ix_store_order_events_order", "order_id", "created_at"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(Integer, ForeignKey("store_orders.id", ondelete="CASCADE"),
                                          nullable=False)
    actor_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    old_status: Mapped[str] = mapped_column(String(20), nullable=False)
    new_status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class StorePanel(Base):
    __tablename__ = "store_panels"
    __table_args__ = (UniqueConstraint("guild_id", "channel_id", "message_id",
                                        name="uq_store_panel_location"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
