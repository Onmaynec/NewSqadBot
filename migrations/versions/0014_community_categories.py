"""Категории ролей и предложений для расширенного интерфейса.

Revision ID: 0014_community_categories
Revises: 0013_announcements
"""
import sqlalchemy as sa
from alembic import op

revision = "0014_community_categories"
down_revision = "0013_announcements"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("role_panel_options") as batch:
        batch.add_column(sa.Column("category", sa.String(20),
                                   server_default="games", nullable=False))
    with op.batch_alter_table("community_suggestions") as batch:
        batch.add_column(sa.Column("category", sa.String(20),
                                   server_default="general", nullable=False))


def downgrade():
    with op.batch_alter_table("community_suggestions") as batch:
        batch.drop_column("category")
    with op.batch_alter_table("role_panel_options") as batch:
        batch.drop_column("category")
