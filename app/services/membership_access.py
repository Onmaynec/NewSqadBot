"""Проверка доступа: роли Discord недостаточно без завершённого Membership."""
from __future__ import annotations

from sqlalchemy import select

from app.models import Application
from app.services.role_rules import is_approved


async def approved_member(db, member, member_role_id: int | None) -> bool:
    if not is_approved(member, member_role_id) or getattr(member, "bot", False):
        return False
    async with db.sessions() as session:
        latest = await session.scalar(select(Application.status).where(
            Application.guild_id == member.guild.id, Application.user_id == member.id
        ).order_by(Application.id.desc()).limit(1))
    return latest == "approved"
