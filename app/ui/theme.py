"""Единый синий стиль карточек, уведомлений и ошибок NewSquad."""
from __future__ import annotations

import logging

import discord

BRAND_BLUE = 0x4A86F7
BRAND_GREEN = BRAND_BLUE  # Совместимость со старыми модулями на переходном этапе.
FOOTER = "NEW SQUAD  •  Панель управления"
NO_MENTIONS = discord.AllowedMentions.none()
BUTTON_PRIMARY = discord.ButtonStyle.primary
BUTTON_SECONDARY = discord.ButtonStyle.secondary
BUTTON_DANGER = discord.ButtonStyle.danger
log = logging.getLogger(__name__)


def card(title: str, description: str = "", *, footer: str = FOOTER) -> discord.Embed:
    """Спокойная карточка с едиными отступами, цветом и подписью."""
    embed = discord.Embed(
        title=title.strip()[:256],
        description=(description or "").strip()[:4096],
        color=BRAND_BLUE,
    )
    if footer:
        embed.set_footer(text=footer[:2048])
    return embed


def info_line(label: str, value: object, *, icon: str = "") -> str:
    """Подпись и значение, устойчивые к длинным Discord-панелям."""
    prefix = f"{icon}  " if icon else ""
    return f"{prefix}**{label}**  ·  {value}"


def section(title: str, text: str) -> str:
    """Заголовок и информация с единым межстрочным ритмом."""
    return f"**{title.strip()}**\n{text.strip()}"


def section_list(*sections: tuple[str, str]) -> str:
    """Отделять смысловые блоки пустой строкой, без псевдографики."""
    return "\n\n".join(section(title, body) for title, body in sections if body.strip())


def status_line(label: str, value: object, *, good: bool | None = None) -> str:
    marker = "●" if good else "○" if good is False else "•"
    return f"{marker}  **{label}:** {value}"


def panel_card(title: str, subtitle: str, *sections: tuple[str, str],
               footer: str = FOOTER) -> discord.Embed:
    """Общий компонент для настроек, справки и карточек участников."""
    description = subtitle.strip()
    details = section_list(*sections)
    if details:
        description = f"{description}\n\n{details}" if description else details
    return card(title, description, footer=footer)


def notice(message: str, *, kind: str = "info") -> discord.Embed:
    titles = {
        "info": "Информация",
        "success": "Действие выполнено",
        "warning": "Требуется внимание",
        "error": "Ошибка выполнения",
    }
    title = titles.get(kind, titles["info"])
    body = discord.utils.escape_mentions(message[:3800])
    symbols = {"info": "ℹ", "success": "✓", "warning": "!", "error": "×"}
    caption = symbols.get(kind, symbols["info"])
    return card(f"NewSquad  /  {title}", f"{caption}  {body}")


async def reply_error(interaction: discord.Interaction, *, trace: str | None = None) -> None:
    """Не раскрывает traceback пользователю, но оставляет ID ошибки в Docker-логах."""
    suffix = f"\n\n**Код события:** `{trace}`" if trace else ""
    payload = notice(
        "Действие не удалось завершить. Повторите попытку или сообщите администрации." + suffix,
        kind="error",
    )
    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=payload, ephemeral=True, allowed_mentions=NO_MENTIONS)
        else:
            await interaction.response.send_message(embed=payload, ephemeral=True, allowed_mentions=NO_MENTIONS)
    except discord.HTTPException:
        log.exception("Discord отклонил ответ об ошибке interaction_id=%s", interaction.id)


async def _deny_other_guild(interaction: discord.Interaction) -> bool:
    from app.core.guild_guard import is_allowed_interaction

    if is_allowed_interaction(interaction):
        return False
    try:
        if interaction.response.is_done():
            await interaction.followup.send(
                embed=notice("Функция доступна только на сервере NewSquad.", kind="warning"),
                ephemeral=True, allowed_mentions=NO_MENTIONS)
        else:
            await interaction.response.send_message(
                embed=notice("Функция доступна только на сервере NewSquad.", kind="warning"),
                ephemeral=True, allowed_mentions=NO_MENTIONS)
    except discord.HTTPException:
        log.warning("Нельзя ответить на кнопку с другого сервера interaction_id=%s", interaction.id)
    return True


class LoggedView(discord.ui.View):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return not await _deny_other_guild(interaction)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        log.error(
            "Ошибка Discord View: view=%s item=%s guild_id=%s user_id=%s interaction_id=%s",
            type(self).__name__, getattr(item, "custom_id", "-"),
            interaction.guild_id, interaction.user.id, interaction.id,
            exc_info=(type(error), error, error.__traceback__),
        )
        await reply_error(interaction, trace=str(interaction.id))


class LoggedModal(discord.ui.Modal):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return not await _deny_other_guild(interaction)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.error(
            "Ошибка Discord Modal: modal=%s guild_id=%s user_id=%s interaction_id=%s",
            type(self).__name__, interaction.guild_id, interaction.user.id,
            interaction.id, exc_info=(type(error), error, error.__traceback__),
        )
        await reply_error(interaction, trace=str(interaction.id))
