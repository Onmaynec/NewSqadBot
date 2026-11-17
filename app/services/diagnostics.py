"""Диагностика настройки NewSqadBot без изменения ролей и каналов."""
from __future__ import annotations

from dataclasses import dataclass

import discord

from app.ui.theme import card


@dataclass(frozen=True, slots=True)
class Diagnostic:
    level: str
    name: str
    description: str


async def collect(bot) -> list[Diagnostic]:
    result: list[Diagnostic] = []
    guild = bot.get_guild(bot.config.guild_id)
    if guild is None:
        return [Diagnostic("error", "Сервер", "Бот не видит целевой сервер.")]
    try:
        ready = await bot.database.ping()
    except Exception:
        ready = False
    result.append(Diagnostic("ok" if ready else "error", "SQLite",
                             "База отвечает." if ready else "База недоступна."))
    candidate_id = await bot.database.get_setting(guild.id, "candidate_role_id")
    candidate_role = guild.get_role(candidate_id) if candidate_id else None
    result.append(Diagnostic(
        "ok" if candidate_role else "error", "Роль кандидата",
        "Правило доступа: отсутствие этой роли." if candidate_role
        else "Настройте корректную роль кандидата в /botsettings.",
    ))
    me = guild.me
    if me is None:
        result.append(Diagnostic("error", "Права бота", "Не найден участник-бот."))
    else:
        perms = me.guild_permissions
        result.append(Diagnostic(
            "ok" if perms.manage_roles else "warning", "Управление ролями",
            "Доступно." if perms.manage_roles else "Нет Manage Roles.",
        ))
        if candidate_role and me.top_role <= candidate_role:
            result.append(Diagnostic(
                "error", "Иерархия ролей",
                "Роль бота должна быть выше роли кандидата.",
            ))
    channels = [
        ("audit_channel_id", "Журнал событий", discord.TextChannel, False),
        ("application_channel_id", "Канал заявок", discord.TextChannel, False),
        ("review_channel_id", "Рассмотрение заявок", discord.TextChannel, False),
        ("voice_lobby_channel_id", "Канал генерации", discord.VoiceChannel, False),
        ("voice_category_channel_id", "Категория голосовых комнат",
         discord.CategoryChannel, True),
        ("ticket_category_channel_id", "Категория тикетов",
         discord.CategoryChannel, True),
    ]
    for key, label, expected, category in channels:
        channel_id = await bot.database.get_setting(guild.id, key)
        channel = guild.get_channel(channel_id) if channel_id else None
        if channel is None:
            result.append(Diagnostic("warning", label, "Не настроен или удалён."))
            continue
        if not isinstance(channel, expected):
            result.append(Diagnostic("error", label, "Выбран неверный тип канала."))
            continue
        if me is None:
            continue
        perms = channel.permissions_for(me)
        if category:
            allowed = perms.manage_channels
        elif expected is discord.VoiceChannel:
            allowed = perms.view_channel and perms.move_members
        else:
            allowed = perms.view_channel and perms.send_messages and perms.embed_links
        if not allowed:
            result.append(Diagnostic("warning", label, "Недостаточно разрешений в канале."))
        else:
            result.append(Diagnostic("ok", label, "Конфигурация доступна."))
    return result


def diagnostic_embed(items: list[Diagnostic]) -> discord.Embed:
    marker = {"ok": "✓", "warning": "!", "error": "×"}
    errors = sum(x.level == "error" for x in items)
    warnings = sum(x.level == "warning" for x in items)
    details = "\n".join(
        f"**{marker.get(item.level, '?')}  {item.name}** — {item.description}"
        for item in items
    )
    return card("NewSquad / Диагностика конфигурации",
                f"**Критических ошибок:** {errors}  ·  **Предупреждений:** {warnings}\n\n"
                f"{details}"[:4000])
