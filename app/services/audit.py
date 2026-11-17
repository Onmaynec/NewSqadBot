"""Безопасные Embed-уведомления в административные журналы."""
from __future__ import annotations

import json
import logging
import time

import discord

from app.models import AuditEntry
from app.ui.mentions import safe_display
from app.ui.theme import NO_MENTIONS, card

log = logging.getLogger(__name__)


async def log_event(bot, *, channel_setting: str, title: str, details: str,
                    actor_id: int = 0, action: str = "event", target_id: int | None = None) -> None:
    # Persist the fact, even if the Discord channel is not configured or unavailable.
    async with bot.database.session() as session:
        session.add(AuditEntry(guild_id=bot.config.guild_id, actor_id=actor_id, action=action,
            data_json=json.dumps({"target_id": target_id, "details": details[:1600]}, ensure_ascii=False),
            created_at=int(time.time())))
    channel_id = await bot.database.get_setting(bot.config.guild_id, channel_setting)
    if not channel_id:
        return
    guild = bot.get_guild(bot.config.guild_id)
    channel = guild.get_channel(channel_id) if guild else None
    if not isinstance(channel, discord.TextChannel):
        log.warning("Журнал %s не настроен или недоступен: %s", channel_setting, channel_id)
        return
    # Пары "Ключ: значение" показываем как отдельные строки, сохраняя @роли.
    # allowed_mentions=none предотвращает уведомления при отображении логов.
    segments = []
    for piece in details.replace("; ", "\n").splitlines():
        piece = safe_display(piece.strip())
        if not piece:
            continue
        label, separator, value = piece.partition(": ")
        segments.append(f"**{label}:** {value}" if separator else piece)
    embed = card(
        f"NewSquad  /  Журнал  /  {title}",
        "\n".join(segments)[:3800] or "Событие зарегистрировано.",
        footer=f"Событие: {action}  •  Журнал сервера",
    )
    embed.timestamp = discord.utils.utcnow()
    if actor_id:
        embed.add_field(name="Инициатор", value=f"<@{actor_id}>", inline=False)
    try:
        await channel.send(embed=embed, allowed_mentions=NO_MENTIONS)
    except discord.HTTPException:
        log.exception("Не удалось опубликовать событие аудита %s", action)
