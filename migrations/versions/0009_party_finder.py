"""Party Finder и фиксация снятия роли кандидата.

Revision ID: 0009_party_finder
Revises: 0008_voice_rooms
"""
from alembic import op
import sqlalchemy as sa

revision = "0009_party_finder"
down_revision = "0008_voice_rooms"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("membership_applications", sa.Column(
        "candidate_role_status", sa.String(length=16), nullable=False, server_default="pending"))
    op.create_table(
        "party_listings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("guild_id", sa.BigInteger, nullable=False),
        sa.Column("owner_id", sa.BigInteger, nullable=False),
        sa.Column("game", sa.String(80), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("capacity", sa.Integer, nullable=False),
        sa.Column("member_count", sa.Integer, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("starts_at", sa.BigInteger),
        sa.Column("expires_at", sa.BigInteger, nullable=False),
        sa.Column("channel_id", sa.BigInteger),
        sa.Column("message_id", sa.BigInteger),
        sa.Column("voice_requested", sa.Boolean, nullable=False),
        sa.Column("voice_channel_id", sa.BigInteger),
        sa.Column("created_at", sa.BigInteger, nullable=False),
        sa.Column("updated_at", sa.BigInteger, nullable=False),
        sa.CheckConstraint("capacity BETWEEN 2 AND 25", name="ck_party_capacity"),
        sa.CheckConstraint("member_count >= 1 AND member_count <= capacity", name="ck_party_count"),
        sa.CheckConstraint("status IN ('open','full','closed','expired')", name="ck_party_status"),
    )
    op.create_index("ix_party_guild_status", "party_listings", ["guild_id", "status", "expires_at"])
    op.create_index("ix_party_owner", "party_listings", ["guild_id", "owner_id", "status"])
    op.create_index(
        "uq_party_owner_active", "party_listings", ["guild_id", "owner_id"],
        unique=True, sqlite_where=sa.text("status IN ('open','full')"))
    op.create_table(
        "party_members",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("party_id", sa.Integer, sa.ForeignKey("party_listings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.BigInteger, nullable=False),
        sa.Column("joined_at", sa.BigInteger, nullable=False),
        sa.UniqueConstraint("party_id", "user_id", name="uq_party_member"),
    )


def downgrade():
    op.drop_table("party_members")
    op.drop_index("uq_party_owner_active", table_name="party_listings")
    op.drop_index("ix_party_owner", table_name="party_listings")
    op.drop_index("ix_party_guild_status", table_name="party_listings")
    op.drop_table("party_listings")
    op.drop_column("membership_applications", "candidate_role_status")
