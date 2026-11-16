"""Временные голосовые комнаты и персональные разрешения доступа.

Revision ID: 0008_voice_rooms
Revises: 0007_quests_achievements
"""
import sqlalchemy as sa
from alembic import op

revision = "0008_voice_rooms"
down_revision = "0007_quests_achievements"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "voice_rooms",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("guild_id", sa.BigInteger, nullable=False),
        sa.Column("channel_id", sa.BigInteger, nullable=False),
        sa.Column("owner_id", sa.BigInteger, nullable=False),
        sa.Column("lobby_id", sa.BigInteger, nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("user_limit", sa.Integer, nullable=False),
        sa.Column("locked", sa.Boolean, nullable=False),
        sa.Column("hidden", sa.Boolean, nullable=False),
        sa.Column("created_at", sa.BigInteger, nullable=False),
        sa.Column("updated_at", sa.BigInteger, nullable=False),
        sa.UniqueConstraint("guild_id", "channel_id", name="uq_voice_room_channel"),
        sa.UniqueConstraint("guild_id", "owner_id", name="uq_voice_room_owner"),
        sa.CheckConstraint("user_limit >= 0 AND user_limit <= 99", name="ck_voice_room_limit"),
    )
    op.create_table(
        "voice_room_access",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("room_id", sa.Integer, sa.ForeignKey("voice_rooms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.BigInteger, nullable=False),
        sa.Column("mode", sa.String(8), nullable=False),
        sa.UniqueConstraint("room_id", "user_id", name="uq_voice_room_access_user"),
        sa.CheckConstraint("mode IN ('allow','deny')", name="ck_voice_room_access_mode"),
    )


def downgrade() -> None:
    op.drop_table("voice_room_access")
    op.drop_table("voice_rooms")
