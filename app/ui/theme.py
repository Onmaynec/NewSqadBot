"""Единый зелёный стиль для Discord Embed и сообщений NewSquad."""
from __future__ import annotations

import logging

import discord

BRAND_GREEN = 0x4CCE91  # близко к цвету имени NewSqadBot на сервере
FOOTER = "NewSquad  •  NewSqadBot"
log = logging.getLogger(__name__)


def card(title: str, description: str = "", *, footer: str = FOOTER) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=BRAND_GREEN)
    if footer:
        embed.set_footer(text=footer)
    return embed


def notice(message: str, *, kind: str = "info") -> discord.Embed:
    labels = {
        "info": ("Информация", "ℹ️"),
        "success": ("Готово", "✅"),
        "warning": ("Внимание", "⚠️"),
        "error": ("Не удалось выполнить действие", "⚠️"),
    }
    label, symbol = labels.get(kind, labels["info"])
    return card(f"{symbol} {label}", discord.utils.escape_mentions(message[:3800]))


async def reply_error(interaction: discord.Interaction, *, trace: str | None = None) -> None:
    """Не раскрывает traceback пользователю, но оставляет ID ошибки для Docker-логов."""
    suffix = f"\nКод события: `{trace}`" if trace else ""
    payload = notice("Не удалось выполнить действие. Повторите попытку или обратитесь к администрации." + suffix,
                     kind="error")
    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=payload, ephemeral=True)
        else:
            await interaction.response.send_message(embed=payload, ephemeral=True)
    except discord.HTTPException:
        log.exception("Discord отклонил ответ об ошибке interaction_id=%s", interaction.id)


class LoggedView(discord.ui.View):
    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        log.error("Ошибка Discord View: view=%s item=%s guild_id=%s user_id=%s interaction_id=%s",
                  type(self).__name__, getattr(item, "custom_id", "-"), interaction.guild_id,
                  interaction.user.id, interaction.id, exc_info=(type(error), error, error.__traceback__))
        await reply_error(interaction, trace=str(interaction.id))


class LoggedModal(discord.ui.Modal):
    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.error("Ошибка Discord Modal: modal=%s guild_id=%s user_id=%s interaction_id=%s",
                  type(self).__name__, interaction.guild_id, interaction.user.id, interaction.id,
                  exc_info=(type(error), error, error.__traceback__))
        await reply_error(interaction, trace=str(interaction.id))
