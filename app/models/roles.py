"""Self-assign role panels, scheduled temporary grants and welcome receipts."""
from sqlalchemy import BigInteger, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class RolePanel(Base):
    __tablename__ = "role_panels"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[int | None] = mapped_column(BigInteger)
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(String(1000), nullable=False, default="Выберите роли в меню ниже.")
    mode: Mapped[str] = mapped_column(String(10), nullable=False, default="multi")
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True)
    created_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class RolePanelOption(Base):
    __tablename__ = "role_panel_options"
    __table_args__ = (UniqueConstraint("panel_id", "role_id", name="uq_panel_role"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    panel_id: Mapped[int] = mapped_column(ForeignKey("role_panels.id", ondelete="CASCADE"), nullable=False)
    role_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    category: Mapped[str] = mapped_column(String(20), nullable=False, default="games")


class TemporaryRole(Base):
    __tablename__ = "temporary_roles"
    __table_args__ = (
        Index("ix_temporary_roles_due", "status", "expires_at"),
        Index("uq_temporary_active", "guild_id", "user_id", "role_id", unique=True,
              sqlite_where=text("status IN ('active','granting')")),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    role_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    granted_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    granted_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expires_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")


class WelcomeDelivery(Base):
    __tablename__ = "welcome_deliveries"
    __table_args__ = (UniqueConstraint("guild_id", "application_id", name="uq_welcome_application"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    application_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    message_id: Mapped[int | None] = mapped_column(BigInteger)
    attempted_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)


class LevelRoleReward(Base):
    __tablename__ = "level_role_rewards"
    __table_args__ = (UniqueConstraint("guild_id", "level", "role_id", name="uq_level_reward"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    role_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
