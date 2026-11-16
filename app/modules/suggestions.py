"""Suggestions: идеи, голосование, обсуждения и решения администрации."""
from __future__ import annotations

import asyncio
import logging
from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands, tasks
from sqlalchemy import select

from app.core.permissions import can_manage_settings
from app.models import MemberProgress
from app.services import suggestions as store
from app.services.audit import log_event
from app.services.membership_access import approved_member
from app.ui.theme import LoggedModal, LoggedView, card, notice

log = logging.getLogger(__name__)
NO_MENTIONS = discord.AllowedMentions.none()
STATES = {
    "new": "Новая",
    "under_review": "На рассмотрении",
    "accepted": "Принята",
    "rejected": "Отклонена",
    "implemented": "Реализована",
}


def safe(value: str) -> str:
    return discord.utils.escape_markdown(discord.utils.escape_mentions(value))


def suggestion_embed(item) -> discord.Embed:
    e = card(f"NewSquad / Предложение #{item.id:04d}",
        f"**Тема:** {safe(item.topic)}\n**Автор:** <@{item.author_id}>\n"
        f"**Описание:**\n{safe(item.description)}")
    e.add_field(name="За", value=str(item.votes_for), inline=True)
    e.add_field(name="Против", value=str(item.votes_against), inline=True)
    e.add_field(name="Статус", value=STATES[item.status], inline=True)
    if item.status_reason:
        e.add_field(name="Решение администрации", value=safe(item.status_reason[:500]), inline=False)
    if item.thread_id:
        e.add_field(name="Обсуждение", value=f"<#{item.thread_id}>", inline=False)
    e.set_footer(text=f"NewSquad • Идея #{item.id:04d}")
    return e


class CreateSuggestionModal(LoggedModal, title="Предложение для NewSquad"):
    topic = discord.ui.TextInput(label="Тема", min_length=4, max_length=100)
    details = discord.ui.TextInput(
        label="Описание идеи", style=discord.TextStyle.paragraph,
        min_length=12, max_length=1200,
    )

    def __init__(self, cog: Suggestions):
        super().__init__(timeout=300)
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.create_idea(interaction, str(self.topic.value), str(self.details.value))


class CreateButton(discord.ui.Button):
    def __init__(self, cog: Suggestions):
        super().__init__(
            label="Предложить идею", style=discord.ButtonStyle.primary,
            custom_id="newsqad:suggest:create",
        )
        self.cog = cog

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await self.cog.enabled() or not await self.cog.allowed(interaction):
            await interaction.response.send_message(
                embed=notice("Для подачи идеи требуется одобренное членство.", kind="warning"),
                ephemeral=True)
            return
        await interaction.response.send_modal(CreateSuggestionModal(self.cog))


class CreatePanel(LoggedView):
    def __init__(self, cog: Suggestions):
        super().__init__(timeout=None)
        self.add_item(CreateButton(cog))


class VoteButton(discord.ui.Button):
    def __init__(self, cog: Suggestions, suggestion_id: int,
                 action: Literal["up", "down", "discuss"], *, closed: bool = False):
        specs = {
            "up": ("За", discord.ButtonStyle.primary),
            "down": ("Против", discord.ButtonStyle.danger),
            "discuss": ("Обсудить", discord.ButtonStyle.secondary),
        }
        label, style = specs[action]
        super().__init__(
            label=label, style=style,
            custom_id=f"newsqad:suggest:{suggestion_id}:{action}",
            disabled=closed and action != "discuss",
        )
        self.cog, self.suggestion_id, self.action = cog, suggestion_id, action

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.cog.action(interaction, self.suggestion_id, self.action)


class SuggestionView(LoggedView):
    def __init__(self, cog: Suggestions, suggestion_id: int, *, closed: bool = False):
        super().__init__(timeout=None)
        for action in ("up", "down", "discuss"):
            self.add_item(VoteButton(cog, suggestion_id, action, closed=closed))


class Suggestions(commands.Cog):
    suggest = app_commands.Group(name="suggest", description="Идеи и голосования NewSquad")

    def __init__(self, bot):
        self.bot = bot
        self._discussion_locks: dict[int, asyncio.Lock] = {}

    async def cog_load(self) -> None:
        self.bot.add_view(CreatePanel(self))
        for item in await store.published(self.bot.database, self.bot.config.guild_id):
            self.bot.add_view(
                SuggestionView(self, item.id, closed=item.status not in store.OPEN_STATES),
                message_id=item.message_id,
            )
        self.bot.events.subscribe(
            "suggestions.panel_deleted", "suggestions.deleted_alert", self._alert_deleted)
        self.bot.events.subscribe(
            "suggestions.status_changed", "suggestions.status_audit", self._audit_status)
        self.republish.start()

    async def cog_unload(self) -> None:
        self.republish.cancel()

    @tasks.loop(minutes=5)
    async def republish(self) -> None:
        for item in await store.unpublished(self.bot.database, self.bot.config.guild_id):
            try:
                await self._publish(item)
            except (discord.HTTPException, RuntimeError):
                log.exception("Не опубликована идея suggestion_id=%s", item.id)

    @republish.before_loop
    async def before_republish(self) -> None:
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        await self.republish()

    async def enabled(self) -> bool:
        return bool(await self.bot.database.get_setting(
            self.bot.config.guild_id, "suggestions_enabled", True))

    def is_staff(self, member: discord.Member) -> bool:
        if member.bot or member.guild.id != self.bot.config.guild_id:
            return False
        perms = getattr(member, "guild_permissions", None)
        return (member.id == member.guild.owner_id or
                member.id in self.bot.config.owner_ids or
                bool(perms and perms.administrator))

    def admin(self, interaction: discord.Interaction) -> bool:
        return can_manage_settings(interaction, self.bot.config.owner_ids)

    async def allowed(self, interaction: discord.Interaction) -> bool:
        if (interaction.guild_id != self.bot.config.guild_id
                or not isinstance(interaction.user, discord.Member)
                or interaction.user.bot):
            return False
        candidate_id = await self.bot.database.get_setting(
            interaction.guild_id, "candidate_role_id",
        )
        return await approved_member(self.bot.database, interaction.user, candidate_id)

    async def check_level(self, member: discord.Member) -> bool:
        if self.is_staff(member):
            return True
        needed = int(await self.bot.database.get_setting(
            member.guild.id, "suggest_min_level", 1))
        if needed <= 1:
            return True
        async with self.bot.database.sessions() as session:
            level = await session.scalar(select(MemberProgress.level).where(
                MemberProgress.guild_id == member.guild.id,
                MemberProgress.user_id == member.id,
            ))
        return (level or 1) >= needed

    async def _publish(self, item) -> bool:
        if item.message_id and not item.panel_missing:
            return True
        guild = self.bot.get_guild(item.guild_id)
        channel = guild.get_channel(item.channel_id) if guild else None
        if not isinstance(channel, discord.TextChannel):
            raise RuntimeError(f"Канал предложений недоступен: {item.channel_id}")
        message = await channel.send(
            embed=suggestion_embed(item),
            view=SuggestionView(self, item.id, closed=item.status not in store.OPEN_STATES),
            allowed_mentions=NO_MENTIONS,
        )
        if not await store.set_message(self.bot.database, item.guild_id, item.id, message.id):
            try:
                await message.delete()
            except discord.HTTPException:
                log.exception("Не удалён дубликат карточки suggestion_id=%s", item.id)
            return False
        self.bot.add_view(
            SuggestionView(self, item.id, closed=item.status not in store.OPEN_STATES),
            message_id=message.id,
        )
        log.info("Предложение опубликовано suggestion_id=%s message_id=%s", item.id, message.id)
        return True

    async def _refresh(self, suggestion_id: int) -> None:
        item = await store.get(self.bot.database, self.bot.config.guild_id, suggestion_id)
        if item is None or item.panel_missing or item.message_id is None:
            return
        guild = self.bot.get_guild(item.guild_id)
        channel = guild.get_channel(item.channel_id) if guild else None
        if not isinstance(channel, discord.TextChannel):
            log.warning("Канал идеи не найден suggestion_id=%s", suggestion_id)
            return
        try:
            message = await channel.fetch_message(item.message_id)
            await message.edit(
                embed=suggestion_embed(item),
                view=SuggestionView(self, item.id, closed=item.status not in store.OPEN_STATES),
                allowed_mentions=NO_MENTIONS,
            )
        except discord.NotFound:
            await store.deleted_panel(
                self.bot.database, item.guild_id, item.channel_id, item.message_id)
        except discord.HTTPException:
            log.exception("Ошибка обновления идеи suggestion_id=%s", suggestion_id)

    async def _alert_deleted(self, payload: dict) -> None:
        if int(payload["guild_id"]) != self.bot.config.guild_id:
            return
        log.warning("Удалена панель Suggestions kind=%s message_id=%s channel_id=%s",
                    payload["kind"], payload["message_id"], payload["channel_id"])
        await log_event(
            self.bot, channel_setting="audit_channel_id",
            title="Потеряна панель предложений",
            details=(f"Тип: {payload['kind']}; объект: {payload['target_id']}; "
                     f"канал: {payload['channel_id']}; сообщение: {payload['message_id']}. "
                     "История сохранена. Команды восстановления: /suggest restore и /suggest panel."),
            actor_id=0, action="suggestions.panel_deleted",
            target_id=payload["target_id"],
        )

    async def _audit_status(self, payload: dict) -> None:
        if int(payload["guild_id"]) != self.bot.config.guild_id:
            return
        await log_event(
            self.bot, channel_setting="audit_channel_id",
            title="Решение по предложению",
            details=(f"Идея #{payload['suggestion_id']:04d}: "
                     f"{STATES[payload['old_status']]} → {STATES[payload['status']]}. "
                     f"Причина: {payload['reason']}"),
            actor_id=payload["actor_id"], action="suggestions.status_changed",
            target_id=payload["suggestion_id"],
        )

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        if payload.guild_id != self.bot.config.guild_id:
            return
        try:
            lost = await store.deleted_panel(
                self.bot.database, payload.guild_id, payload.channel_id, payload.message_id)
            if lost:
                log.warning("Удалена карточка Suggestions kind=%s message_id=%s",
                            lost, payload.message_id)
        except Exception:
            log.exception("Не удалось отметить удаление панели message_id=%s", payload.message_id)

    async def create_idea(self, interaction: discord.Interaction,
                          topic: str, description: str) -> None:
        if not await self.enabled() or not await self.allowed(interaction):
            await interaction.response.send_message(
                embed=notice("Только для одобренных участников.", kind="warning"),
                ephemeral=True)
            return
        if not await self.check_level(interaction.user):
            await interaction.response.send_message(
                embed=notice("Недостаточный уровень для публикации.", kind="warning"),
                ephemeral=True)
            return
        channel_id = await self.bot.database.get_setting(
            self.bot.config.guild_id, "suggest_channel_id")
        guild = interaction.guild
        channel = guild.get_channel(channel_id) if guild and channel_id else None
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                embed=notice("Канал предложений ещё не настроен.", kind="warning"),
                ephemeral=True)
            return
        cooldown = int(await self.bot.database.get_setting(
            guild.id, "suggest_cooldown_seconds", 1800))
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            item = await store.create(
                self.bot.database, guild_id=guild.id, author_id=interaction.user.id,
                channel_id=channel.id, topic=topic, description=description,
                cooldown=0 if self.is_staff(interaction.user) else cooldown,
            )
        except ValueError as exc:
            await interaction.followup.send(
                embed=notice(str(exc), kind="warning"), ephemeral=True)
            return
        try:
            published = await self._publish(item)
        except (discord.HTTPException, RuntimeError):
            log.exception("Не опубликована идея suggestion_id=%s", item.id)
            published = False
        message = (f"Идея #{item.id:04d} опубликована." if published else
                   f"Идея #{item.id:04d} сохранена. Публикация будет повторена.")
        await interaction.followup.send(
            embed=notice(message, kind="success" if published else "warning"), ephemeral=True)

    async def action(self, interaction: discord.Interaction, suggestion_id: int,
                     action: str) -> None:
        if not await self.enabled() or not await self.allowed(interaction):
            await interaction.response.send_message(
                embed=notice("Требуется одобренное членство.", kind="warning"),
                ephemeral=True)
            return
        if action == "discuss":
            await self._discuss(interaction, suggestion_id)
            return
        if action not in ("up", "down"):
            await interaction.response.send_message(
                embed=notice("Неизвестная кнопка.", kind="warning"), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            changed, item = await store.vote(
                self.bot.database, guild_id=interaction.guild_id,
                suggestion_id=suggestion_id, user_id=interaction.user.id,
                choice=1 if action == "up" else -1,
            )
        except ValueError as exc:
            await interaction.followup.send(embed=notice(str(exc), kind="warning"), ephemeral=True)
            return
        if changed:
            await self._refresh(suggestion_id)
        await interaction.followup.send(embed=notice(
            f"{'Голос учтён' if changed else 'Этот голос уже учтён'}. "
            f"За: {item.votes_for} · Против: {item.votes_against}", kind="success"),
            ephemeral=True)

    async def _discuss(self, interaction: discord.Interaction, suggestion_id: int) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        lock = self._discussion_locks.setdefault(suggestion_id, asyncio.Lock())
        async with lock:
            item = await store.get(self.bot.database, interaction.guild_id, suggestion_id)
            if item is None:
                result = "Предложение не найдено."
            elif item.panel_missing or not item.message_id:
                result = "Карточка удалена. Обратитесь к администрации для восстановления."
            else:
                guild = interaction.guild
                thread = guild.get_thread(item.thread_id) if item.thread_id else None
                if thread is None and item.thread_id:
                    try:
                        thread = await guild.fetch_channel(item.thread_id)
                    except discord.NotFound:
                        thread = None
                    except discord.HTTPException:
                        log.exception("Ошибка поиска треда suggestion_id=%s", item.id)
                        await interaction.followup.send(embed=notice(
                            "Не удалось открыть обсуждение.", kind="warning"), ephemeral=True)
                        return
                if isinstance(thread, discord.Thread):
                    result = f"Обсуждение: {thread.mention}"
                else:
                    channel = guild.get_channel(item.channel_id)
                    if not isinstance(channel, discord.TextChannel):
                        result = "Канал предложений недоступен."
                    else:
                        try:
                            message = await channel.fetch_message(item.message_id)
                            thread = await message.create_thread(
                                name=f"Обсуждение #{item.id} • {item.topic}"[:100],
                                auto_archive_duration=1440,
                                reason=f"[NSB-SUGGEST-{item.id}] Обсуждение",
                            )
                            await store.set_thread(
                                self.bot.database, guild.id, item.id, thread.id,
                                previous=item.thread_id)
                            await self._refresh(item.id)
                            result = f"Обсуждение: {thread.mention}"
                        except discord.Forbidden:
                            result = "У бота нет разрешения Create Public Threads."
                        except discord.HTTPException:
                            log.exception("Не создан тред suggestion_id=%s", item.id)
                            result = "Discord не разрешил создать обсуждение."
        await interaction.followup.send(
            embed=notice(result), ephemeral=True, allowed_mentions=NO_MENTIONS)

    @suggest.command(name="create", description="Предложить идею сообществу")
    async def create_command(self, interaction: discord.Interaction,
                             topic: str, description: str) -> None:
        await self.create_idea(interaction, topic, description)

    @suggest.command(name="list", description="Последние предложения и их статусы")
    async def list_command(self, interaction: discord.Interaction,
                           status: Literal["new", "under_review", "accepted",
                                           "rejected", "implemented"] | None = None) -> None:
        if not await self.allowed(interaction):
            await interaction.response.send_message(
                embed=notice("Только для участников.", kind="warning"), ephemeral=True)
            return
        rows = await store.list_items(self.bot.database, interaction.guild_id, status=status)
        lines = [
            f"**#{item.id:04d}** · {safe(item.topic[:75])} · "
            f"{item.votes_for} за / {item.votes_against} против · {STATES[item.status]}"
            for item in rows
        ]
        await interaction.response.send_message(
            embed=card("NewSquad / Предложения", "\n".join(lines) or "Пока нет предложений."),
            ephemeral=True, allowed_mentions=NO_MENTIONS)

    @suggest.command(name="view", description="Карточка идеи и история решений")
    async def view_command(self, interaction: discord.Interaction, number: int) -> None:
        if not await self.allowed(interaction):
            await interaction.response.send_message(
                embed=notice("Только для участников.", kind="warning"), ephemeral=True)
            return
        item = await store.get(self.bot.database, interaction.guild_id, number)
        if item is None:
            await interaction.response.send_message(
                embed=notice("Предложение не найдено.", kind="warning"), ephemeral=True)
            return
        embed = suggestion_embed(item)
        history = await store.history(self.bot.database, interaction.guild_id, number)
        if history:
            lines = [
                f"<t:{entry.created_at}:d> · {STATES[entry.new_status]} · {safe(entry.reason[:100])}"
                for entry in history[-5:]
            ]
            embed.add_field(name="История решений", value="\n".join(lines), inline=False)
        if item.panel_missing:
            embed.add_field(name="Публикация", value="Панель удалена; история сохранена.", inline=False)
        await interaction.response.send_message(
            embed=embed, ephemeral=True, allowed_mentions=NO_MENTIONS)

    @suggest.command(name="status", description="Решение администрации с пояснением")
    @app_commands.choices(status=[
        app_commands.Choice(name="Новая", value="new"),
        app_commands.Choice(name="На рассмотрении", value="under_review"),
        app_commands.Choice(name="Принята", value="accepted"),
        app_commands.Choice(name="Отклонена", value="rejected"),
        app_commands.Choice(name="Реализована", value="implemented"),
    ])
    async def status_command(self, interaction: discord.Interaction, number: int,
                             status: app_commands.Choice[str], reason: str) -> None:
        if not self.admin(interaction):
            await interaction.response.send_message(
                embed=notice("Только администрация.", kind="warning"), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            item = await store.change_status(
                self.bot.database, guild_id=interaction.guild_id,
                suggestion_id=number, actor_id=interaction.user.id,
                status=status.value, reason=reason,
            )
        except ValueError as exc:
            await interaction.followup.send(embed=notice(str(exc), kind="warning"), ephemeral=True)
            return
        await self._refresh(item.id)
        await interaction.followup.send(embed=notice(
            f"Идея #{item.id:04d}: {STATES[item.status]}. Пояснение сохранено.", kind="success"),
            ephemeral=True)

    @suggest.command(name="settings", description="Настройки и состояние панелей")
    async def settings_command(self, interaction: discord.Interaction) -> None:
        if not self.admin(interaction):
            await interaction.response.send_message(
                embed=notice("Только администрация.", kind="warning"), ephemeral=True)
            return
        gid = interaction.guild_id
        channel_id = await self.bot.database.get_setting(gid, "suggest_channel_id")
        min_level = await self.bot.database.get_setting(gid, "suggest_min_level", 1)
        cooldown = await self.bot.database.get_setting(gid, "suggest_cooldown_seconds", 1800)
        missing, missing_create = await store.missing_count(self.bot.database, gid)
        embed = card("NewSquad / Suggestions — настройки",
            f"**Приём идей:** {'включён' if await self.enabled() else 'отключён'}\n"
            f"**Канал:** {f'<#{channel_id}>' if channel_id else 'не настроен'}\n"
            f"**Минимальный уровень:** {min_level}\n"
            f"**Задержка:** {cooldown // 60} минут\n"
            f"**Удалено карточек идей:** {missing}\n"
            f"**Удалено панелей подачи:** {missing_create}\n\n"
            "Настройка: /suggest configure; публикация: /suggest panel; "
            "восстановление удалённой идеи: /suggest restore.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @suggest.command(name="configure", description="Канал, уровень и задержка публикаций")
    async def configure_command(self, interaction: discord.Interaction,
                                channel: discord.TextChannel, enabled: bool = True,
                                min_level: app_commands.Range[int, 1, 1000] = 1,
                                cooldown_minutes: app_commands.Range[int, 0, 1440] = 30) -> None:
        if not self.admin(interaction) or channel.guild.id != self.bot.config.guild_id:
            await interaction.response.send_message(
                embed=notice("Только администрация NewSquad.", kind="warning"), ephemeral=True)
            return
        values = {
            "suggest_channel_id": channel.id,
            "suggestions_enabled": enabled,
            "suggest_min_level": min_level,
            "suggest_cooldown_seconds": cooldown_minutes * 60,
        }
        for key, value in values.items():
            await self.bot.database.set_setting(
                interaction.guild_id, key, value, actor_id=interaction.user.id)
        await interaction.response.send_message(
            embed=notice("Настройки предложений сохранены.", kind="success"), ephemeral=True)

    @suggest.command(name="panel", description="Разместить панель подачи идей")
    async def panel_command(self, interaction: discord.Interaction) -> None:
        if not self.admin(interaction):
            await interaction.response.send_message(
                embed=notice("Только администрация.", kind="warning"), ephemeral=True)
            return
        channel_id = await self.bot.database.get_setting(
            interaction.guild_id, "suggest_channel_id")
        channel = interaction.guild.get_channel(channel_id) if channel_id else None
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(embed=notice(
                "Сначала назначьте канал через /suggest configure.", kind="warning"), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            message = await channel.send(
                embed=card("NewSquad / Предложить идею",
                    "Есть идея для развития сервера? Нажми кнопку, расскажи, что изменить. "
                    "Другие участники смогут проголосовать и обсудить предложение."),
                view=CreatePanel(self), allowed_mentions=NO_MENTIONS)
        except discord.HTTPException:
            log.exception("Не удалось создать панель Suggestions")
            await interaction.followup.send(
                embed=notice("Discord не разрешил создать панель.", kind="warning"), ephemeral=True)
            return
        await store.register_panel(
            self.bot.database, interaction.guild_id, channel.id, message.id)
        await interaction.followup.send(
            embed=notice("Панель предложений опубликована.", kind="success"), ephemeral=True)

    @suggest.command(name="restore", description="Восстановить карточку удалённой идеи")
    async def restore_command(self, interaction: discord.Interaction, number: int) -> None:
        if not self.admin(interaction):
            await interaction.response.send_message(
                embed=notice("Только администрация.", kind="warning"), ephemeral=True)
            return
        item = await store.get(self.bot.database, interaction.guild_id, number)
        if item is None or not item.panel_missing:
            await interaction.response.send_message(
                embed=notice("Идея не найдена или её карточка не удалена.", kind="warning"),
                ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            restored = await self._publish(item)
        except (discord.HTTPException, RuntimeError):
            log.exception("Не восстановлена идея suggestion_id=%s", number)
            restored = False
        await interaction.followup.send(
            embed=notice(
                "Карточка восстановлена. История и голоса сохранены." if restored
                else "Восстановление не удалось. Проверьте права бота.",
                kind="success" if restored else "warning",
            ), ephemeral=True)


async def setup(bot) -> None:
    await bot.add_cog(Suggestions(bot), guild=discord.Object(id=bot.config.guild_id))
