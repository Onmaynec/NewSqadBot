"""Add isolated seller-managed storefront, orders and audit.

Revision ID: 0018_private_store
Revises: 0017_member_support_voice
"""
import sqlalchemy as sa
from alembic import op

revision = "0018_private_store"
down_revision = "0017_member_support_voice"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "store_products",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("sku", sa.String(40), nullable=False),
        sa.Column("title", sa.String(90), nullable=False),
        sa.Column("description", sa.String(600), nullable=False, server_default=""),
        sa.Column("category", sa.String(40), nullable=False, server_default="digital"),
        sa.Column("price_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="RUB"),
        sa.Column("stock", sa.Integer()),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("price_minor > 0 AND (stock IS NULL OR stock >= 0)",
                           name="ck_store_product_price_stock"),
        sa.UniqueConstraint("guild_id", "sku", name="uq_store_product_sku"),
    )
    op.create_index("ix_store_products_public", "store_products",
                    ["guild_id", "active", "sort_order"])
    op.create_table(
        "store_orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("store_products.id"), nullable=False),
        sa.Column("interaction_id", sa.BigInteger(), nullable=False),
        sa.Column("sku", sa.String(40), nullable=False),
        sa.Column("product_title", sa.String(90), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("price_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="awaiting_payment"),
        sa.Column("staff_message_id", sa.BigInteger()),
        sa.Column("staff_channel_id", sa.BigInteger()),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.Column("completed_at", sa.BigInteger()),
        sa.CheckConstraint("status IN ('awaiting_payment','payment_reported','processing','completed','cancelled')",
                           name="ck_store_order_status"),
        sa.CheckConstraint("quantity BETWEEN 1 AND 10 AND price_minor > 0",
                           name="ck_store_order_quantity"),
        sa.UniqueConstraint("guild_id", "interaction_id", name="uq_store_checkout_interaction"),
    )
    op.create_index("ix_store_orders_owner", "store_orders",
                    ["guild_id", "user_id", "created_at"])
    op.create_index("ix_store_orders_queue", "store_orders",
                    ["guild_id", "status", "created_at"])
    op.create_table(
        "store_order_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey(
            "store_orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_id", sa.BigInteger(), nullable=False),
        sa.Column("old_status", sa.String(20), nullable=False),
        sa.Column("new_status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
    )
    op.create_index("ix_store_order_events_order", "store_order_events",
                    ["order_id", "created_at"])
    op.create_table(
        "store_panels",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("guild_id", "channel_id", "message_id", name="uq_store_panel_location"),
    )


def downgrade():
    op.drop_table("store_panels")
    op.drop_index("ix_store_order_events_order", table_name="store_order_events")
    op.drop_table("store_order_events")
    op.drop_index("ix_store_orders_queue", table_name="store_orders")
    op.drop_index("ix_store_orders_owner", table_name="store_orders")
    op.drop_table("store_orders")
    op.drop_index("ix_store_products_public", table_name="store_products")
    op.drop_table("store_products")
