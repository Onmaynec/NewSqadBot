"""Безопасный поиск и изменение дел модерации.

SQL-операции не выполняют Discord API: Discord-изменения должны подтверждаться
вызывающим Cog, который держит per-case lock.
"""
from __future__ import annotations

import time

from sqlalchemy import select, update

from app.models import ModerationAction, ModerationCase


async def find(db, guild_id: int, *, number: int | None = None,
               user_id: int | None = None, kind: str | None = None,
               status: str | None = None, limit: int = 20) -> list[ModerationCase]:
    if not 1 <= limit <= 50:
        raise ValueError("Лимит истории: 1–50")
    allowed_kinds = {"ban", "mute", "warn", "kick"}
    if kind is not None and kind not in allowed_kinds:
        raise ValueError("Неизвестный тип дела")
    async with db.sessions() as session:
        query = select(ModerationCase).where(ModerationCase.guild_id == guild_id)
        if number is not None:
            query = query.where(ModerationCase.id == number)
        if user_id is not None:
            query = query.where(ModerationCase.user_id == user_id)
        if kind is not None:
            query = query.where(ModerationCase.kind == kind)
        if status == "active":
            query = query.where(ModerationCase.status.in_(("active", "pending", "external_active")))
        elif status == "closed":
            query = query.where(~ModerationCase.status.in_(("active", "pending", "external_active")))
        elif status is not None:
            raise ValueError("Статус должен быть active, closed или all")
        rows = await session.scalars(query.order_by(ModerationCase.id.desc()).limit(limit))
        return list(rows.all())


async def actions(db, guild_id: int, number: int, *, limit: int = 15) -> list[ModerationAction]:
    if not await find(db, guild_id, number=number):
        return []
    async with db.sessions() as session:
        rows = await session.scalars(select(ModerationAction).where(
            ModerationAction.case_id == number).order_by(ModerationAction.id.desc()).limit(limit))
        return list(rows.all())


async def apply(db, guild_id: int, number: int, *, actor_id: int, action: str,
                details: str, changes: dict, expected_status: str = "active") -> bool:
    """CAS: только одно изменение состояния активного дела; журнал в той же транзакции."""
    if action not in {"reason_changed", "duration_changed", "revoked"}:
        raise ValueError("Неизвестное изменение дела")
    if not 3 <= len(details.strip()) <= 400:
        raise ValueError("Укажите причину изменения от 3 до 400 символов")
    allowed_columns = {"reason", "expires_at", "status", "closed_by", "completed_at"}
    if not changes or not set(changes) <= allowed_columns:
        raise ValueError("Недопустимые поля дела")
    async with db.session() as session:
        result = await session.execute(update(ModerationCase).where(
            ModerationCase.guild_id == guild_id, ModerationCase.id == number,
            ModerationCase.status == expected_status,
        ).values(**changes))
        if result.rowcount != 1:
            return False
        session.add(ModerationAction(
            case_id=number, actor_id=actor_id, action=action,
            details=details.strip(), created_at=int(time.time())))
        return True
