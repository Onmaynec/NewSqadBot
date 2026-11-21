"""Shared moderation targeting rules for Discord commands and the local portal.

Pure policy: no Discord I/O and no mutable state. The caller still validates
Discord permissions for each concrete ban/kick/timeout operation.
"""
from __future__ import annotations


def audit_reason(case_id: int, reason: str) -> str:
    if case_id <= 0:
        raise ValueError("Некорректный номер дела.")
    return f"[NSB-{case_id:05d}] {reason}"[:512]


def denied_target(*, actor, guild, target_id: int, target=None, bot_member=None,
                  privileged: bool = False) -> str | None:
    if target_id in (actor.id, guild.owner_id):
        return "Нельзя наказать себя или владельца сервера."
    if target is not None:
        if target.guild_permissions.administrator:
            return "Нельзя наказать администратора сервера."
        if not privileged and target.top_role >= actor.top_role:
            return "Недостаточно прав по иерархии ролей участника."
        if bot_member is not None and target.top_role >= bot_member.top_role:
            return "Роль бота ниже роли участника."
    return None
