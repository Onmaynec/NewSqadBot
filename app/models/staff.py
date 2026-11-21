"""v2.0.0: local staff workspace metadata (no ticket transcripts)."""
from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class StaffTask(Base):
    __tablename__ = "staff_tasks"
    __table_args__ = (
        Index("ix_staff_tasks_assignee", "guild_id", "assigned_to", "status"),
        CheckConstraint("status IN ('open','done')", name="ck_staff_task_status"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    assigned_to: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(String(600), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="open")
    due_at: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class StaffTemplate(Base):
    __tablename__ = "staff_templates"
    __table_args__ = (UniqueConstraint("guild_id", "scope", "title", name="uq_staff_template"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    body: Mapped[str] = mapped_column(String(1500), nullable=False)
    updated_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class StaffOperation(Base):
    __tablename__ = "staff_operations"
    __table_args__ = (
        UniqueConstraint("guild_id", "request_key", "target_kind", "target_id",
                         name="uq_staff_operation_idempotency"),
        Index("ix_staff_operations_history", "guild_id", "created_at"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    request_key: Mapped[str] = mapped_column(String(72), nullable=False)
    target_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    target_id: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="processing")
    summary: Mapped[str] = mapped_column(String(240), nullable=False, default="")
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
