"""Награды за достижения, ежедневные и еженедельные задания.

Revision ID: 0007_quests_achievements
Revises: 0006_economy
"""
import sqlalchemy as sa
from alembic import op

revision = "0007_quests_achievements"
down_revision = "0006_economy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "quest_claims",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("guild_id", sa.BigInteger, nullable=False),
        sa.Column("user_id", sa.BigInteger, nullable=False),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("period", sa.String(10), nullable=False),
        sa.Column("frequency", sa.String(8), nullable=False),
        sa.Column("xp_reward", sa.Integer, nullable=False),
        sa.Column("coin_reward", sa.Integer, nullable=False),
        sa.Column("claimed_at", sa.BigInteger, nullable=False),
        sa.UniqueConstraint("guild_id", "user_id", "code", "period", name="uq_quest_claim_once"),
        sa.CheckConstraint("xp_reward >= 0 AND coin_reward >= 0", name="ck_quest_rewards_nonnegative"),
    )
    op.create_index("ix_quest_claims_user", "quest_claims", ["guild_id", "user_id", "claimed_at"])
    op.create_table(
        "achievement_unlocks",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("guild_id", sa.BigInteger, nullable=False),
        sa.Column("user_id", sa.BigInteger, nullable=False),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("unlocked_at", sa.BigInteger, nullable=False),
        sa.Column("claimed_at", sa.BigInteger),
        sa.UniqueConstraint("guild_id", "user_id", "code", name="uq_achievement_once"),
    )
    op.create_index("ix_achievement_user", "achievement_unlocks", ["guild_id", "user_id", "unlocked_at"])


def downgrade() -> None:
    op.drop_index("ix_achievement_user", table_name="achievement_unlocks")
    op.drop_table("achievement_unlocks")
    op.drop_index("ix_quest_claims_user", table_name="quest_claims")
    op.drop_table("quest_claims")
