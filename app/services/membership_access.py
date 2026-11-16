"""Единая авторизация: отсутствие Discord-роли кандидата — доступ открыт."""
from __future__ import annotations

from app.services.role_rules import is_approved


async def approved_member(db, member, candidate_role_id: int | None) -> bool:
    """Совместимый интерфейс без запросов к истории заявок Membership.

    db оставлен для существующих вызывающих модулей. Проверяется только
    роль кандидата, кроме блокировки ботов и отсутствия настройки роли.
    """
    return is_approved(member, candidate_role_id)
