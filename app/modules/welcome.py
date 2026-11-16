"""Welcome approved members without exposing the closed server to applicants."""
from __future__ import annotations

import logging
import re
import time

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.core.permissions import can_manage_settings
from app.models import WelcomeDelivery
from app.services.audit import log_event
from app.services.membership_access import approved_member
from app.ui.theme import BRAND_BLUE

log = logging.getLogger(__name__)

WELCOME_TEMPLATE = "Добро пожаловать в {server}, {user}! Теперь ты часть NewSquad."
FAREWELL_TEMPLATE = "{username} покинул(а) {server}."
ALLOWED_VARIABLES = frozenset({"user", "username", "server", "member_count"})
PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")


def validate_template(template: str) -> str:
    template = template.strip()
    if not template or len(template) > 1500:
        raise ValueError("Текст должен содержать от 1 до 1500 символов.")
    unknown = set(PLACEHOLDER.findall(template)) - ALLOWED_VARIABLES
    if unknown:
        raise ValueError(f"Неизвестная переменная: {', '.join(sorted(unknown))}")
    return template


def render_template(template: str, *, member: discord.Member) -> str:
    """No user-controlled format expressions, only known placeholders."""
    data = {
        "user": member.mention,
        "username": discord.utils.escape_mentions(discord.utils.escape_markdown(member.name)),
        "server": discord.utils.escape_mentions(discord.utils.escape_markdown(member.guild.name)),
        "member_count": str(member.guild.member_count or 0),
    }
    return PLACEHOLDER.sub(lambda m: data.get(m.group(1), m.group(0)), template)


def welcome_embed(text: str, *, member: discord.Member | None = None, title: str) -> discord.Embed:
    embed = discord.Embed(title=f"NewSquad  /  {title}", description=text.strip(), color=BRAND_BLUE)
    embed.set_footer(text="NewSquad • Рады видеть тебя среди своих")
    if member is not None:
        embed.set_thumbnail(url=member.display_avatar.url)
    return embed


class Welcome(commands.Cog):
    welcome = app_commands.Group(name="welcome", description="Приветствия и сообщения NewSquad")

    def __init__(self, bot):
        self.bot = bot
        self.bot.events.subscribe("membership.approved", "welcome.post_approval", self.on_approved)

    async def _member_role(self, guild_id: int) -> int | None:
        return await self.bot.database.get_setting(guild_id, "candidate_role_id")

    def _guild(self, guild_id: int) -> discord.Guild | None:
        if guild_id != self.bot.config.guild_id:
            return None
        return self.bot.get_guild(guild_id)

    async def _links(self, guild: discord.Guild) -> discord.ui.View | None:
        rules_id = await self.bot.database.get_setting(guild.id, "rules_channel_id")
        panel_id = await self.bot.database.get_setting(guild.id, "welcome_roles_panel_message_id")
        links = []
        if rules_id and guild.get_channel(rules_id):
            links.append(("Правила", f"https://discord.com/channels/{guild.id}/{rules_id}"))
        if panel_id:
            role_channel_id = await self.bot.database.get_setting(guild.id, "welcome_roles_panel_channel_id")
            if role_channel_id and guild.get_channel(role_channel_id):
                links.append(("Выбрать роли", f"https://discord.com/channels/{guild.id}/{role_channel_id}/{panel_id}"))
        if not links:
            return None
        view = discord.ui.View(timeout=180)
        for label, url in links:
            view.add_item(discord.ui.Button(label=label, url=url, style=discord.ButtonStyle.link))
        return view

    async def on_approved(self, payload: dict) -> None:
        guild_id, user_id = int(payload["guild_id"]), int(payload["user_id"])
        application_id = int(payload["application_id"])
        guild = self._guild(guild_id)
        if guild is None:
            raise RuntimeError("Сервер ещё не доступен после переподключения")
        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except discord.NotFound:
                return  # candidate left before delivery
        if not await approved_member(self.bot.database, member, await self._member_role(guild_id)):
            raise RuntimeError("Подтверждённая роль ещё не отображается у участника")
        if not await self.bot.database.get_setting(guild_id, "welcome_enabled", True):
            return
        channel_id = await self.bot.database.get_setting(guild_id, "welcome_channel_id")
        channel = guild.get_channel(channel_id) if channel_id else None
        if not isinstance(channel, discord.TextChannel):
            return  # no public welcome until the admin configures an approved-members channel
        now = int(time.time())
        async with self.bot.database.session() as session:
            insert = sqlite_insert(WelcomeDelivery).values(
                guild_id=guild_id, application_id=application_id, user_id=user_id,
                status="pending", attempted_at=0)
            await session.execute(insert.on_conflict_do_nothing(index_elements=[
                WelcomeDelivery.guild_id, WelcomeDelivery.application_id]))
            entry = await session.scalar(select(WelcomeDelivery).where(
                WelcomeDelivery.guild_id == guild_id,
                WelcomeDelivery.application_id == application_id))
            if entry.status == "sent":
                return
            if entry.status == "sending" and entry.attempted_at > now - 300:
                return
            # Atomic per-application claim; a pending retry cannot send concurrently.
            claimed = await session.execute(update(WelcomeDelivery).where(
                WelcomeDelivery.id == entry.id, WelcomeDelivery.status == entry.status,
                WelcomeDelivery.attempted_at == entry.attempted_at,
            ).values(status="sending", attempted_at=now))
            if claimed.rowcount != 1:
                return
        template = await self.bot.database.get_setting(guild_id, "welcome_template", WELCOME_TEMPLATE)
        message = render_template(template, member=member)
        try:
            sent = await channel.send(
                embed=welcome_embed(message, member=member, title="Добро пожаловать в NewSquad"),
                view=await self._links(guild),
                allowed_mentions=discord.AllowedMentions(users=[member], everyone=False, roles=False),
            )
        except discord.HTTPException as exc:
            async with self.bot.database.session() as session:
                await session.execute(update(WelcomeDelivery).where(
                    WelcomeDelivery.guild_id == guild_id, WelcomeDelivery.application_id == application_id,
                ).values(status="failed", error=str(exc)[:500]))
            raise
        async with self.bot.database.session() as session:
            await session.execute(update(WelcomeDelivery).where(
                WelcomeDelivery.guild_id == guild_id, WelcomeDelivery.application_id == application_id,
            ).values(status="sent", message_id=sent.id, error=None))
        await log_event(self.bot, channel_setting="membership_audit_channel_id",
            title="Участник приветствован", details=f"Участник: {user_id}; заявка: #{application_id:04d}",
            actor_id=0, action="welcome.sent", target_id=user_id)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.guild.id != self.bot.config.guild_id or getattr(member, "pending", False):
            return
        # Candidate receives only the instructions for applying; no public greeting or Member role.
        if not await self.bot.database.get_setting(member.guild.id, "welcome_candidate_dm", False):
            return
        application_channel_id = await self.bot.database.get_setting(member.guild.id, "application_channel_id")
        if not application_channel_id:
            return
        try:
            await member.send(
                f"Добро пожаловать! Чтобы вступить в NewSquad, подайте заявку в <#{application_channel_id}>.",
                allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            log.debug("ЛС кандидата %s закрыты", member.id)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        if member.guild.id != self.bot.config.guild_id:
            return
        guild = member.guild
        if not await self.bot.database.get_setting(guild.id, "welcome_farewell_enabled", False):
            return
        if not await approved_member(self.bot.database, member, await self._member_role(guild.id)):
            return
        channel_id = await self.bot.database.get_setting(guild.id, "farewell_channel_id")
        channel = guild.get_channel(channel_id) if channel_id else None
        if not isinstance(channel, discord.TextChannel):
            return
        template = await self.bot.database.get_setting(guild.id, "farewell_template", FAREWELL_TEMPLATE)
        try:
            await channel.send(embed=welcome_embed(render_template(template, member=member), title="NewSquad · До встречи"),
                allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            log.exception("Не удалось отправить сообщение о выходе %s", member.id)

    async def _admin(self, interaction: discord.Interaction) -> bool:
        if can_manage_settings(interaction, self.bot.config.owner_ids):
            return True
        await interaction.response.send_message("Нужны права администратора NewSquad.", ephemeral=True)
        return False

    @welcome.command(name="settings", description="Посмотреть настройки приветствий")
    async def settings(self, interaction: discord.Interaction) -> None:
        if not await self._admin(interaction):
            return
        db = self.bot.database
        keys = ("welcome_channel_id", "farewell_channel_id", "rules_channel_id",
                "welcome_enabled", "welcome_farewell_enabled", "welcome_candidate_dm",
                "welcome_template", "farewell_template")
        details = [f"`{key}`: {str(await db.get_setting(interaction.guild_id, key, 'не настроено'))[:200]}" for key in keys]
        await interaction.response.send_message("\n".join(details)[:1900], ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none())

    @welcome.command(name="template", description="Изменить шаблон приветствия или прощания")
    @app_commands.choices(kind=[app_commands.Choice(name="Приветствие", value="welcome"),
                                  app_commands.Choice(name="Прощание", value="farewell")])
    async def template(self, interaction: discord.Interaction, kind: app_commands.Choice[str], text: str) -> None:
        if not await self._admin(interaction):
            return
        try:
            text = validate_template(text)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await self.bot.database.set_setting(interaction.guild_id, f"{kind.value}_template", text, actor_id=interaction.user.id)
        await interaction.response.send_message("Шаблон сохранён.", ephemeral=True)

    @welcome.command(name="enable", description="Включить или отключить приветствия, прощания, ЛС кандидатам")
    @app_commands.choices(kind=[app_commands.Choice(name="Приветствие", value="welcome_enabled"),
                                  app_commands.Choice(name="Прощание", value="welcome_farewell_enabled"),
                                  app_commands.Choice(name="ЛС кандидатам", value="welcome_candidate_dm")])
    async def enable(self, interaction: discord.Interaction, kind: app_commands.Choice[str], enabled: bool) -> None:
        if not await self._admin(interaction):
            return
        await self.bot.database.set_setting(interaction.guild_id, kind.value, enabled, actor_id=interaction.user.id)
        await interaction.response.send_message("Настройка сохранена.", ephemeral=True)

    @welcome.command(name="preview", description="Предварительный просмотр сообщения")
    @app_commands.choices(kind=[app_commands.Choice(name="Приветствие", value="welcome"),
                                  app_commands.Choice(name="Прощание", value="farewell")])
    async def preview(self, interaction: discord.Interaction, kind: app_commands.Choice[str]) -> None:
        if not await self._admin(interaction):
            return
        template = await self.bot.database.get_setting(interaction.guild_id, f"{kind.value}_template",
            WELCOME_TEMPLATE if kind.value == "welcome" else FAREWELL_TEMPLATE)
        member = interaction.user
        title = "Добро пожаловать в NewSquad" if kind.value == "welcome" else "NewSquad · До встречи"
        await interaction.response.send_message(embed=welcome_embed(render_template(template, member=member), member=member, title=title),
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @welcome.command(name="test", description="Опубликовать проверочное сообщение в указанном канале")
    async def test(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        if not await self._admin(interaction):
            return
        if channel.guild.id != interaction.guild_id:
            await interaction.response.send_message("Нужен канал NewSquad.", ephemeral=True)
            return
        template = await self.bot.database.get_setting(interaction.guild_id, "welcome_template", WELCOME_TEMPLATE)
        try:
            await channel.send(embed=welcome_embed(render_template(template, member=interaction.user),
                member=interaction.user, title="NewSquad · Тест приветствия"),
                allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException as exc:
            await interaction.response.send_message(f"Не удалось отправить тест: {exc}", ephemeral=True)
            return
        await interaction.response.send_message(f"Тест отправлен в {channel.mention}.", ephemeral=True)


async def setup(bot) -> None:
    await bot.add_cog(Welcome(bot), guild=discord.Object(id=bot.config.guild_id))
