"""Кошельки, неизменяемый журнал валюты, магазин и инвентарь.

Revision ID: 0006_economy
Revises: 0005_progression_profiles
"""
import sqlalchemy as sa
from alembic import op

revision = "0006_economy"
down_revision = "0005_progression_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "economy_wallets",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("guild_id", sa.BigInteger, nullable=False),
        sa.Column("user_id", sa.BigInteger, nullable=False),
        sa.Column("balance", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("earned", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("spent", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("last_daily", sa.String(10), nullable=False, server_default=""),
        sa.Column("daily_streak", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.BigInteger, nullable=False),
        sa.Column("updated_at", sa.BigInteger, nullable=False),
        sa.UniqueConstraint("guild_id", "user_id", name="uq_economy_wallet"),
        sa.CheckConstraint("balance >= 0 AND earned >= 0 AND spent >= 0", name="ck_economy_wallet_nonnegative"),
    )
    op.create_index("ix_economy_wallet_rank", "economy_wallets", ["guild_id", "balance"])
    op.create_table(
        "economy_ledger",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("guild_id", sa.BigInteger, nullable=False),
        sa.Column("user_id", sa.BigInteger, nullable=False),
        sa.Column("amount", sa.BigInteger, nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("reference", sa.String(180), nullable=False),
        sa.Column("actor_id", sa.BigInteger),
        sa.Column("created_at", sa.BigInteger, nullable=False),
        sa.UniqueConstraint("guild_id", "reference", name="uq_economy_reference"),
        sa.CheckConstraint("amount != 0", name="ck_economy_ledger_nonzero"),
    )
    op.create_index("ix_economy_user_ledger", "economy_ledger", ["guild_id", "user_id", "created_at"])
    op.create_table(
        "economy_shop_items",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("guild_id", sa.BigInteger, nullable=False),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("title", sa.String(80), nullable=False),
        sa.Column("description", sa.String(300), nullable=False, server_default=""),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("price", sa.Integer, nullable=False),
        sa.Column("role_id", sa.BigInteger),
        sa.Column("stock", sa.Integer),
        sa.Column("sold", sa.Integer, nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.BigInteger, nullable=False),
        sa.UniqueConstraint("guild_id", "code", name="uq_economy_item_code"),
        sa.CheckConstraint("price > 0 AND sold >= 0 AND (stock IS NULL OR stock >= 1)", name="ck_economy_stock"),
    )
    op.create_table(
        "economy_inventory",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("guild_id", sa.BigInteger, nullable=False),
        sa.Column("user_id", sa.BigInteger, nullable=False),
        sa.Column("item_id", sa.Integer, sa.ForeignKey("economy_shop_items.id"), nullable=False),
        sa.Column("equipped", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("delivery", sa.String(16), nullable=False, server_default="not_required"),
        sa.Column("purchased_at", sa.BigInteger, nullable=False),
        sa.UniqueConstraint("guild_id", "user_id", "item_id", name="uq_economy_ownership"),
    )
    op.create_index("ix_economy_inventory_user", "economy_inventory", ["guild_id", "user_id"])


def downgrade() -> None:
    op.drop_index("ix_economy_inventory_user", table_name="economy_inventory")
    op.drop_table("economy_inventory")
    op.drop_table("economy_shop_items")
    op.drop_index("ix_economy_user_ledger", table_name="economy_ledger")
    op.drop_table("economy_ledger")
    op.drop_index("ix_economy_wallet_rank", table_name="economy_wallets")
    op.drop_table("economy_wallets")
