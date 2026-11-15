"""Welcome and role management.

Revision ID: 0003_welcome_roles
Revises: 0002_membership_moderation
"""
import sqlalchemy as sa
from alembic import op

revision = "0003_welcome_roles"
down_revision = "0002_membership_moderation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("role_panels",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("guild_id", sa.BigInteger, nullable=False),
        sa.Column("channel_id", sa.BigInteger, nullable=False),
        sa.Column("message_id", sa.BigInteger),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("description", sa.String(1000), nullable=False, server_default="Выберите роли в меню ниже."),
        sa.Column("mode", sa.String(10), nullable=False, server_default="multi"),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.BigInteger, nullable=False),
        sa.Column("created_at", sa.BigInteger, nullable=False),
        sa.CheckConstraint("mode IN ('single', 'multi')", name="ck_panel_mode"),
    )
    op.create_table("role_panel_options",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("panel_id", sa.Integer, sa.ForeignKey("role_panels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role_id", sa.BigInteger, nullable=False),
        sa.Column("label", sa.String(100), nullable=False),
        sa.UniqueConstraint("panel_id", "role_id", name="uq_panel_role"),
    )
    op.create_table("temporary_roles",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("guild_id", sa.BigInteger, nullable=False),
        sa.Column("user_id", sa.BigInteger, nullable=False),
        sa.Column("role_id", sa.BigInteger, nullable=False),
        sa.Column("granted_by", sa.BigInteger, nullable=False),
        sa.Column("granted_at", sa.BigInteger, nullable=False),
        sa.Column("expires_at", sa.BigInteger, nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
    )
    op.create_index("ix_temporary_roles_due", "temporary_roles", ["status", "expires_at"])
    op.create_index("uq_temporary_active", "temporary_roles", ["guild_id", "user_id", "role_id"],
                    unique=True, sqlite_where=sa.text("status IN ('active','granting')"))
    op.create_table("level_role_rewards",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("guild_id", sa.BigInteger, nullable=False),
        sa.Column("level", sa.Integer, nullable=False),
        sa.Column("role_id", sa.BigInteger, nullable=False),
        sa.UniqueConstraint("guild_id", "level", "role_id", name="uq_level_reward"),
        sa.CheckConstraint("level BETWEEN 1 AND 10000", name="ck_level_reward"),
    )
    op.create_table("welcome_deliveries",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("guild_id", sa.BigInteger, nullable=False),
        sa.Column("application_id", sa.BigInteger, nullable=False),
        sa.Column("user_id", sa.BigInteger, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("message_id", sa.BigInteger),
        sa.Column("attempted_at", sa.BigInteger, nullable=False),
        sa.Column("error", sa.Text),
        sa.UniqueConstraint("guild_id", "application_id", name="uq_welcome_application"),
    )


def downgrade() -> None:
    op.drop_table("welcome_deliveries")
    op.drop_table("level_role_rewards")
    op.drop_index("uq_temporary_active", table_name="temporary_roles")
    op.drop_index("ix_temporary_roles_due", table_name="temporary_roles")
    op.drop_table("temporary_roles")
    op.drop_table("role_panel_options")
    op.drop_table("role_panels")
