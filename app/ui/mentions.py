"""Человекочитаемые упоминания ролей и каналов в Discord-интерфейсе.

ID остаются в базе и технических журналах, но пользователям отображаются
упоминания активных объектов. Для удалённых ролей не создаём ложных пингов.
"""
from __future__ import annotations

import discord


def role_mention(role: discord.Role | None, *, role_id: int | None = None,
                 guild: discord.Guild | None = None) -> str:
    """Вернуть @роль, если она существует; иначе понятное состояние настройки."""
    if role is None and guild is not None and role_id:
        role = guild.get_role(role_id)
    if role is not None:
        return role.mention
    if role_id:
        return f"Роль удалена или недоступна (ID \`{role_id}\`)"
    return "Не назначена"


def role_list(guild: discord.Guild | None, role_ids: list[int] | set[int] | tuple[int, ...],
              *, empty: str = "Нет") -> str:
    values = [role_mention(None, role_id=rid, guild=guild) for rid in sorted(set(role_ids))]
    return ", ".join(values) if values else empty


def channel_mention(guild: discord.Guild | None, channel_id: int | None) -> str:
    if not channel_id:
        return "Не выбран"
    channel = guild.get_channel(channel_id) if guild else None
    if channel is None:
        return f"Канал удалён или недоступен (ID \`{channel_id}\`)"
    if isinstance(channel, discord.CategoryChannel):
        return f"**{discord.utils.escape_markdown(channel.name)}** (категория)"
    return channel.mention


def member_mention(member: discord.Member | None = None, *,
                   user_id: int | None = None) -> str:
    if member is not None:
        return member.mention
    return f"<@{user_id}>" if user_id else "Не указан"


def safe_display(text: str) -> str:
    """Экранировать массовые упоминания, не ломая @роли, @людей и #каналы."""
    return text.replace("@everyone", "@\u200beveryone").replace("@here", "@\u200bhere")
