"""Объявления от имени NewSqadBot: /ann с двумя полями в модальном окне."""
from __future__ import annotations

import logging
import re
from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands

from app.config import VERSION
from app.core.permissions import can_manage_settings
from app.services.audit import log_event
from app.ui.theme import BRAND_BLUE, LoggedModal, NO_MENTIONS, notice

log = logging.getLogger(__name__)

MAX_TITLE = 120
MAX_EMBED_BODY = 3500
MAX_TEXT_BODY = 1750
HEX_COLOR = re.compile(r"#?[0-9a-fA-F]{6}\Z")


def parse_color(raw: str | None) -> int:
    """Только RGB HEX: #RRGGBB или RRGGBB. Цвет по умолчанию — брендовый."""
    if raw is None or not raw.strip():
        return BRAND_BLUE
    value = raw.strip()
    if not HEX_COLOR.fullmatch(value):
        raise ValueError("Цвет укажите в формате #RRGGBB, например #4A86F7.")
    return int(value.lstrip("#"), 16)


def build_announcement(
    kind: Literal["text", "embed"], title: str, body: str,
    color: int = BRAND_BLUE,
) -> dict:
    """Формирует сообщение без массовых пингов и без потери авторской разметки текста."""
    title, body = title.strip(), body.strip()
    if not title or len(title) > MAX_TITLE or "\n" in title or "\r" in title:
        raise ValueError("Заголовок должен быть одной строкой от 1 до 120 символов.")
    max_body = MAX_EMBED_BODY if kind == "embed" else MAX_TEXT_BODY
    if not body or len(body) > max_body:
        raise ValueError(f"Текст объявления должен содержать от 1 до {max_body} символов.")
    if kind == "embed":
        embed = discord.Embed(
            title=discord.utils.escape_mentions(title),
            description=discord.utils.escape_mentions(body),
            color=color,
        )
        embed.set_author(name="NEW SQUAD  /  ОБЪЯВЛЕНИЕ")
        embed.set_footer(text=f"NewSqadBot  •  Версия {VERSION}")
        return {"embed": embed, "allowed_mentions": NO_MENTIONS}
    if kind == "text":
        safe_title = discord.utils.escape_markdown(discord.utils.escape_mentions(title))
        text = f"## {safe_title}\n\n{discord.utils.escape_mentions(body)}"
        if len(text) > 2000:
            raise ValueError("Сообщение длиннее лимита Discord — используйте Embed.")
        return {"content": text, "allowed_mentions": NO_MENTIONS}
    raise ValueError("Неизвестный формат объявления.")


class AnnouncementModal(LoggedModal):
    def __init__(self, cog: Announcements, *, channel_id: int, kind: str, color: int):
        super().__init__(title="NewSquad  /  Новое объявление", timeout=600)
        self.cog, self.channel_id, self.kind, self.color = cog, channel_id, kind, color
        self.headline = discord.ui.TextInput(
            style=discord.TextStyle.short, placeholder="Например: NewSqadBot v1.1.0",
            required=True, min_length=1, max_length=MAX_TITLE,
        )
        self.body = discord.ui.TextInput(
            style=discord.TextStyle.paragraph,
            placeholder="Что нового? Укажите изменения, исправления и важные детали.",
            required=True, min_length=1,
            max_length=MAX_EMBED_BODY if kind == "embed" else MAX_TEXT_BODY,
        )
        # discord.py 2.7: два Label на одном экране, без лишних вопросов.
        self.add_item(discord.ui.Label(text="Заголовок", component=self.headline))
        self.add_item(discord.ui.Label(text="Текст объявления", component=self.body))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.publish(
            interaction, channel_id=self.channel_id, kind=self.kind,
            color=self.color, title=self.headline.value, body=self.body.value,
        )


class Announcements(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def admin(self, interaction: discord.Interaction) -> bool:
        return can_manage_settings(interaction, self.bot.config.owner_ids)

    def destination_problem(
        self, interaction: discord.Interaction, channel: discord.TextChannel, kind: str,
    ) -> str | None:
        if interaction.guild_id != self.bot.config.guild_id or interaction.guild is None:
            return "Команда доступна только на сервере NewSquad."
        if channel.guild.id != self.bot.config.guild_id:
            return "Выбран канал другого сервера."
        guild = interaction.guild
        member = guild.me
        if member is None:
            return "Не удалось проверить права бота. Переподключитесь и повторите попытку."
        perms = channel.permissions_for(member)
        if not perms.view_channel or not perms.send_messages:
            return "Бот не может просматривать этот канал или отправлять в него сообщения."
        if kind == "embed" and not perms.embed_links:
            return "Для отправки Embed необходимы права «Встраивать ссылки» (Embed Links)."
        return None

    @app_commands.command(name="ann", description="Опубликовать объявление от имени бота")
    @app_commands.describe(
        вид="Обычный текст или оформленная Embed-карточка",
        канал="Канал, в котором будет опубликовано объявление",
        цвет="Необязательно: HEX #RRGGBB. По умолчанию синий #4A86F7",
    )
    @app_commands.choices(вид=[
        app_commands.Choice(name="Обычный текст", value="text"),
        app_commands.Choice(name="Embed-карточка", value="embed"),
    ])
    async def ann(
        self, interaction: discord.Interaction, вид: app_commands.Choice[str],
        канал: discord.TextChannel, цвет: str | None = None,
    ) -> None:
        if not self.admin(interaction):
            await interaction.response.send_message(
                embed=notice("Объявления могут публиковать только администраторы.", kind="warning"),
                ephemeral=True,
            )
            return
        if вид.value == "text" and цвет is not None:
            await interaction.response.send_message(
                embed=notice("Цвет можно указать только для Embed-карточки.", kind="warning"),
                ephemeral=True,
            )
            return
        try:
            color = parse_color(цвет)
        except ValueError as exc:
            await interaction.response.send_message(
                embed=notice(str(exc), kind="warning"), ephemeral=True,
            )
            return
        problem = self.destination_problem(interaction, канал, вид.value)
        if problem is not None:
            await interaction.response.send_message(
                embed=notice(problem, kind="warning"), ephemeral=True,
            )
            return
        await interaction.response.send_modal(AnnouncementModal(
            self, channel_id=канал.id, kind=вид.value, color=color,
        ))

    async def publish(
        self, interaction: discord.Interaction, *, channel_id: int, kind: str,
        color: int, title: str, body: str,
    ) -> None:
        """Проверка прав повторяется при отправке Modal, а не только при её открытии."""
        if not self.admin(interaction):
            await interaction.response.send_message(
                embed=notice("Права на публикацию изменились. Объявление не отправлено.", kind="warning"),
                ephemeral=True,
            )
            return
        guild = interaction.guild
        channel = guild.get_channel(channel_id) if guild else None
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                embed=notice("Канал больше недоступен. Откройте /ann заново.", kind="warning"),
                ephemeral=True,
            )
            return
        problem = self.destination_problem(interaction, channel, kind)
        if problem:
            await interaction.response.send_message(
                embed=notice(problem, kind="warning"), ephemeral=True,
            )
            return
        try:
            payload = build_announcement(kind, title, body, color)
        except ValueError as exc:
            await interaction.response.send_message(
                embed=notice(str(exc), kind="warning"), ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            message = await channel.send(**payload)
        except discord.Forbidden:
            log.warning("Объявление запрещено Discord: guild=%s channel=%s actor=%s",
                        guild.id, channel.id, interaction.user.id)
            await interaction.followup.send(
                embed=notice("Discord отказал в публикации: проверьте права бота.", kind="error"),
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            log.exception("Сбой отправки объявления: guild=%s channel=%s actor=%s interaction=%s",
                          guild.id, channel.id, interaction.user.id, interaction.id)
            await interaction.followup.send(
                embed=notice("Не удалось подтвердить отправку. Проверьте канал перед повторной публикацией.",
                             kind="error"),
                ephemeral=True,
            )
            return

        log.info("Объявление отправлено: guild=%s channel=%s message=%s actor=%s kind=%s",
                 guild.id, channel.id, message.id, interaction.user.id, kind)
        try:
            await log_event(
                self.bot, channel_setting="audit_channel_id",
                title="Опубликовано объявление",
                details=(f"Автор: <@{interaction.user.id}>\nКанал: <#{channel.id}>\n"
                         f"Формат: {kind}\nСообщение: {message.id}"),
                actor_id=interaction.user.id, action="ann.published", target_id=message.id,
            )
        except Exception:
            # Повторно отправлять сообщение нельзя: Discord уже подтвердил публикацию.
            log.exception("Объявление отправлено, но запись аудита не удалась message=%s", message.id)
        await interaction.followup.send(
            embed=notice(f"Объявление опубликовано в {channel.mention}.\n"
                         f"[Перейти к сообщению]({message.jump_url})", kind="success"),
            ephemeral=True, allowed_mentions=NO_MENTIONS,
        )


async def setup(bot) -> None:
    await bot.add_cog(Announcements(bot), guild=discord.Object(id=bot.config.guild_id))
