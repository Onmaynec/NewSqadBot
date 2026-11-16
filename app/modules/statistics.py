"""Статистика сервера: дневная активность, персональные данные и рейтинги."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import date
from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands, tasks

from app.core.permissions import can_manage_settings
from app.services import statistics as store
from app.services.membership_access import approved_member
from app.services.progression import progress_bar, threshold
from app.ui.theme import card, notice

log = logging.getLogger(__name__)
NO_MENTIONS = discord.AllowedMentions.none()


def voice_duration(minutes: int) -> str:
    return f"{minutes // 60} ч {minutes % 60:02d} мин." if minutes >= 60 else f"{minutes} мин."


def graph(rows: list[tuple[str, int, int, int, int]],
          period: str, metric: str) -> str:
    index = {"messages": 1, "voice": 2, "joins": 3, "leaves": 4}[metric]
    groups: dict[str, int] = {}
    for row in rows:
        d = date.fromisoformat(row[0])
        if period == "7d":
            label = d.strftime("%d.%m")
        elif period == "30d":
            iso = d.isocalendar()
            label = f"{iso.year}-W{iso.week:02d}"
        else:
            label = d.strftime("%Y-%m")
        groups[label] = groups.get(label, 0) + row[index]
    maximum = max(groups.values(), default=0)
    output = []
    for label, count in groups.items():
        width = count * 12 // maximum if maximum else 0
        output.append(f"{label}: {'▰' * width}{'▱' * (12 - width)} {count}")
    return "\n".join(output)[:1600] or "Нет зарегистрированной активности."


class Statistics(commands.Cog):
    stats = app_commands.Group(name="stats", description="Статистика сервера NewSquad")

    def __init__(self, bot):
        self.bot = bot
        self._lock = asyncio.Lock()
        self._ready = False
        self._timezone = bot.config.timezone

    async def cog_load(self) -> None:
        self.voice_tick.start()
        self.panel_tick.start()
        log.info("Statistics: учёт сообщений без сохранения текста, хранение дневных агрегатов")

    async def cog_unload(self) -> None:
        self.voice_tick.cancel()
        self.panel_tick.cancel()

    async def enabled(self) -> bool:
        return bool(await self.bot.database.get_setting(
            self.bot.config.guild_id, "statistics_enabled", True))

    async def init_tracking(self) -> bool:
        if not await self.enabled():
            return False
        if self._ready:
            return True
        async with self._lock:
            if self._ready:
                return True
            gid = self.bot.config.guild_id
            since = await self.bot.database.get_setting(gid, "statistics_started_at")
            if since is None:
                since = int(time.time())
                await self.bot.database.set_setting(
                    gid, "statistics_started_at", since, actor_id=0)
                await self.bot.database.set_setting(
                    gid, "statistics_timezone", self.bot.config.timezone, actor_id=0)
            self._timezone = await self.bot.database.get_setting(
                gid, "statistics_timezone", self.bot.config.timezone)
            guild = self.bot.get_guild(gid)
            if guild:
                await store.bootstrap_presence(
                    self.bot.database, gid, [member.id for member in guild.members])
            self._ready = True
            log.info("Statistics начала учёт: guild=%s since=%s timezone=%s",
                     gid, since, self._timezone)
            return True

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if await self.init_tracking():
            await self.refresh_panels()

    def admin(self, interaction: discord.Interaction) -> bool:
        return can_manage_settings(interaction, self.bot.config.owner_ids)

    async def allowed(self, interaction: discord.Interaction) -> bool:
        if (interaction.guild_id != self.bot.config.guild_id
                or not isinstance(interaction.user, discord.Member) or interaction.user.bot):
            return False
        if self.admin(interaction):
            return True
        role_id = await self.bot.database.get_setting(interaction.guild_id, "candidate_role_id")
        return await approved_member(self.bot.database, interaction.user, role_id)

    async def approved_participant(self, member: discord.Member) -> bool:
        if member.bot or member.guild.id != self.bot.config.guild_id:
            return False
        if (member.id == member.guild.owner_id
                or member.id in self.bot.config.owner_ids
                or member.guild_permissions.administrator):
            return True
        role = await self.bot.database.get_setting(member.guild.id, "candidate_role_id")
        return await approved_member(self.bot.database, member, role)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if (not message.guild or message.guild.id != self.bot.config.guild_id
                or message.author.bot or message.webhook_id
                or message.type != discord.MessageType.default
                or not isinstance(message.author, discord.Member)):
            return
        if not await self.init_tracking():
            return
        if not await self.bot.database.get_setting(
                message.guild.id, "statistics_messages_enabled", True):
            return
        if not await self.approved_participant(message.author):
            return
        try:
            await store.record_activity(
                self.bot.database, guild_id=message.guild.id, user_id=message.author.id,
                kind="message", reference=message.id,
                timestamp=int(message.created_at.timestamp()), timezone=self._timezone)
        except Exception:
            log.exception("Ошибка учёта сообщения guild=%s user=%s message=%s",
                          message.guild.id, message.author.id, message.id)

    @tasks.loop(minutes=1)
    async def voice_tick(self) -> None:
        if not await self.init_tracking() or not await self.bot.database.get_setting(
                self.bot.config.guild_id, "statistics_voice_enabled", True):
            return
        guild = self.bot.get_guild(self.bot.config.guild_id)
        if guild is None:
            return
        minute = int(time.time()) // 60
        for channel in guild.voice_channels:
            if channel == guild.afk_channel:
                continue
            eligible = []
            for member in channel.members:
                if (member.bot or not member.voice
                        or member.voice.channel != channel
                        or member.voice.deaf or member.voice.self_deaf):
                    continue
                if await self.approved_participant(member):
                    eligible.append(member)
            if len(eligible) < 2:
                continue
            for member in eligible:
                try:
                    await store.record_activity(
                        self.bot.database, guild_id=guild.id, user_id=member.id,
                        kind="voice", reference=minute,
                        timestamp=minute * 60, timezone=self._timezone)
                except Exception:
                    log.exception("Ошибка голосовой статистики user=%s minute=%s",
                                  member.id, minute)

    @voice_tick.before_loop
    async def before_voice_tick(self) -> None:
        await self.bot.wait_until_ready()
        await asyncio.sleep(60)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.guild.id == self.bot.config.guild_id and await self.init_tracking():
            await store.record_presence(
                self.bot.database, guild_id=member.guild.id, user_id=member.id,
                joined=True, timezone=self._timezone)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        if member.guild.id == self.bot.config.guild_id and await self.init_tracking():
            await store.record_presence(
                self.bot.database, guild_id=member.guild.id, user_id=member.id,
                joined=False, timezone=self._timezone)

    async def period(self, key: str) -> store.Period:
        await self.init_tracking()
        return store.period_bounds(key, timezone=self._timezone)

    async def server_embed(self, guild: discord.Guild, key: str) -> discord.Embed:
        period = await self.period(key)
        gid = guild.id
        role_id = await self.bot.database.get_setting(gid, "candidate_role_id")
        candidate_id = await self.bot.database.get_setting(gid, "candidate_role_id")
        started = int(await self.bot.database.get_setting(
            gid, "statistics_started_at", int(time.time())))
        totals = await store.server_totals(
            self.bot.database, guild_id=gid, period=period,
            members=list(guild.members),
            member_count=guild.member_count if guild.member_count is not None else len(guild.members),
            member_role_id=role_id, candidate_role_id=candidate_id,
            tracking_started_at=started)
        embed = card("NewSquad / Статистика сервера",
            f"**Период:** {period.first} — {period.last} ({period.timezone})\n"
            f"**Всего участников:** {totals.total}\n"
            f"**Одобренных Membership:** {totals.approved}\n"
            f"**Кандидатов с ролью:** {totals.candidates}\n"
            f"**Уникальных активных:** {totals.active_users}")
        embed.add_field(name="Состав",
            value=f"Вступило: **{totals.joins}**\nВышло: **{totals.leaves}**\n"
                  f"Одобрено: **{totals.approvals}**\nОтказано: **{totals.rejections}**")
        embed.add_field(name="Активность",
            value=f"Сообщений: **{totals.messages}**\nГолос: **{voice_duration(totals.voice_minutes)}**")
        embed.add_field(name="Сообщество",
            value=f"Мероприятий: **{totals.events}**\n"
                  f"Групп Party Finder: **{totals.parties}**\nТикетов: **{totals.tickets}**",
            inline=False)
        embed.set_footer(text="Данные собираются с момента включения статистики, без восстановления старых сообщений.")
        return embed

    async def activity_embed(self, guild: discord.Guild, key: str) -> discord.Embed:
        period = await self.period(key)
        series = await store.activity_series(self.bot.database, guild.id, period)
        embed = card("NewSquad / Динамика активности",
            f"**Период:** {period.first} — {period.last} ({period.timezone})\n"
            "7 дней — по дням, 30 дней — по неделям, 90 дней — по месяцам.")
        embed.add_field(name="Сообщения", value=graph(series, key, "messages"), inline=False)
        embed.add_field(name="Голосовые минуты", value=graph(series, key, "voice"), inline=False)
        embed.add_field(name="Вступления", value=graph(series, key, "joins"), inline=False)
        return embed

    @stats.command(name="server", description="Сводка по участникам и активности")
    async def server(self, interaction: discord.Interaction,
                     period: Literal["7d", "30d", "90d"] = "7d") -> None:
        if not await self.allowed(interaction) or not await self.enabled():
            await interaction.response.send_message(
                embed=notice("Статистика недоступна.", kind="warning"), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await interaction.followup.send(
            embed=await self.server_embed(interaction.guild, period),
            ephemeral=True, allowed_mentions=NO_MENTIONS)

    @stats.command(name="user", description="Личная статистика участника")
    async def user(self, interaction: discord.Interaction,
                   member: discord.Member | None = None) -> None:
        if not await self.allowed(interaction) or not await self.enabled():
            await interaction.response.send_message(
                embed=notice("Статистика недоступна.", kind="warning"), ephemeral=True)
            return
        member = member or interaction.user
        if member.guild.id != self.bot.config.guild_id or member.bot:
            await interaction.response.send_message(
                embed=notice("Неверный участник.", kind="warning"), ephemeral=True)
            return
        self_view = interaction.user.id == member.id
        admin_view = self.admin(interaction)
        result = await store.personal_totals(
            self.bot.database, guild_id=member.guild.id, user_id=member.id,
            include_balance=self_view or admin_view)
        if not result.public and not self_view and not admin_view:
            await interaction.response.send_message(
                embed=notice("Пользователь скрыл свою статистику.", kind="warning"),
                ephemeral=True)
            return
        description = (
            f"**Участник:** {member.mention}\n"
            f"**Уровень:** {result.level} · **XP:** {result.xp}/{threshold(result.level + 1)}\n"
            f"{progress_bar(result.xp)}\n"
            f"**Засчитано сообщений (XP):** {result.credited_messages}\n"
            f"**Голосовых минут (XP):** {result.credited_voice_minutes}\n"
            f"**Сообщений с запуска Statistics:** {result.tracked_messages}\n"
            f"**Голос с запуска Statistics:** {voice_duration(result.tracked_voice_minutes)}\n"
            f"**Достижения:** {result.achievements} · **Задания:** {result.quests}\n"
            f"**Регистрации в ивентах:** {result.event_registrations}\n"
            f"**Подтверждённых участий:** {result.event_attendance}\n"
            f"**Групп Party Finder:** {result.parties}"
        )
        if result.wallet_balance is not None:
            description += f"\n**Личный баланс:** {result.wallet_balance} NS Coins"
        await interaction.response.send_message(
            embed=card("NewSquad / Личная статистика", description),
            ephemeral=True, allowed_mentions=NO_MENTIONS)

    @stats.command(name="top", description="Рейтинг по XP, сообщениям и голосу")
    async def top(self, interaction: discord.Interaction,
                  metric: Literal["xp", "messages", "voice"] = "xp",
                  period: Literal["7d", "30d", "90d"] = "7d") -> None:
        if not await self.allowed(interaction) or not await self.enabled():
            await interaction.response.send_message(
                embed=notice("Только одобренные участники.", kind="warning"), ephemeral=True)
            return
        if not self.admin(interaction) and not await self.bot.database.get_setting(
                interaction.guild_id, "statistics_public_ranking", True):
            await interaction.response.send_message(
                embed=notice("Публичные рейтинги выключены.", kind="warning"),
                ephemeral=True)
            return
        guild = interaction.guild
        role_id = await self.bot.database.get_setting(guild.id, "candidate_role_id")
        approved = await store.approved_ids(
            self.bot.database, guild.id, list(guild.members), role_id)
        bounds = await self.period(period)
        rows = await store.leaderboard(
            self.bot.database, guild_id=guild.id,
            period=bounds, metric=metric, active_user_ids=approved)
        title = {"xp": "XP (за всё время)", "messages": "Сообщения", "voice": "Голосовые минуты"}[metric]
        lines = [f"**{i}.** <@{uid}> — **{score}**"
                 for i, (uid, score) in enumerate(rows, 1)]
        embed = card(f"NewSquad / Рейтинг · {title}",
            ("\n".join(lines) or "Пока нет результатов.") +
            ("\n\nXP — накопительный показатель." if metric == "xp"
             else f"\n\nПериод: {bounds.first} — {bounds.last}."))
        embed.set_footer(text="Пользователи со скрытыми профилями в рейтинг не входят.")
        await interaction.response.send_message(embed=embed, ephemeral=True, allowed_mentions=NO_MENTIONS)

    @stats.command(name="activity", description="Динамика сообщений и голосовой активности")
    async def activity(self, interaction: discord.Interaction,
                       period: Literal["7d", "30d", "90d"] = "7d") -> None:
        if not await self.allowed(interaction) or not await self.enabled():
            await interaction.response.send_message(
                embed=notice("Статистика недоступна.", kind="warning"), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await interaction.followup.send(
            embed=await self.activity_embed(interaction.guild, period),
            ephemeral=True, allowed_mentions=NO_MENTIONS)

    @stats.command(name="settings", description="Настройки Statistics (администрация)")
    async def settings(self, interaction: discord.Interaction) -> None:
        if not self.admin(interaction):
            await interaction.response.send_message(
                embed=notice("Только администрация.", kind="warning"), ephemeral=True)
            return
        gid = interaction.guild_id
        enabled = await self.enabled()
        messages = await self.bot.database.get_setting(gid, "statistics_messages_enabled", True)
        voice = await self.bot.database.get_setting(gid, "statistics_voice_enabled", True)
        ranking = await self.bot.database.get_setting(gid, "statistics_public_ranking", True)
        started = await self.bot.database.get_setting(gid, "statistics_started_at")
        timezone = await self.bot.database.get_setting(
            gid, "statistics_timezone", self.bot.config.timezone)
        panels = await store.panels(self.bot.database, gid)
        await interaction.response.send_message(embed=card("NewSquad / Statistics",
            f"**Учёт:** {'включён' if enabled else 'отключён'}\n"
            f"**Сообщения:** {'да' if messages else 'нет'}\n"
            f"**Голос:** {'да' if voice else 'нет'}\n"
            f"**Публичный рейтинг:** {'да' if ranking else 'нет'}\n"
            f"**Начало учёта:** {f'<t:{started}:f>' if started else 'ещё не начат'}\n"
            f"**Часовой пояс:** {timezone}\n"
            f"**Панелей:** {len(panels)}\n\n"
            "Изменение: /stats configure · Публикация: /stats panel"),
            ephemeral=True)

    @stats.command(name="configure", description="Включить учёт и управление рейтингами")
    async def configure(self, interaction: discord.Interaction, enabled: bool = True,
                        messages: bool = True, voice: bool = True,
                        public_ranking: bool = True) -> None:
        if not self.admin(interaction):
            await interaction.response.send_message(
                embed=notice("Только администрация.", kind="warning"), ephemeral=True)
            return
        for name, value in (
            ("statistics_enabled", enabled),
            ("statistics_messages_enabled", messages),
            ("statistics_voice_enabled", voice),
            ("statistics_public_ranking", public_ranking),
        ):
            await self.bot.database.set_setting(
                interaction.guild_id, name, value, actor_id=interaction.user.id)
        if enabled:
            await self.init_tracking()
        await interaction.response.send_message(
            embed=notice("Настройки сохранены.", kind="success"), ephemeral=True)

    @stats.command(name="panel", description="Создать обновляемую панель статистики")
    async def panel(self, interaction: discord.Interaction, channel: discord.TextChannel,
                    kind: Literal["server", "activity"] = "server") -> None:
        if not self.admin(interaction) or channel.guild.id != self.bot.config.guild_id:
            await interaction.response.send_message(
                embed=notice("Только администрация.", kind="warning"), ephemeral=True)
            return
        if not await self.enabled():
            await interaction.response.send_message(
                embed=notice("Сначала включите учёт.", kind="warning"), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        existing = next((p for p in await store.panels(
            self.bot.database, interaction.guild_id)
            if p.channel_id == channel.id and p.kind == kind), None)
        embed = (await self.server_embed(interaction.guild, "7d") if kind == "server"
                 else await self.activity_embed(interaction.guild, "7d"))
        message = None
        if existing:
            try:
                message = await channel.fetch_message(existing.message_id)
                await message.edit(embed=embed, allowed_mentions=NO_MENTIONS)
            except discord.NotFound:
                message = None
        if message is None:
            message = await channel.send(embed=embed, allowed_mentions=NO_MENTIONS)
        await store.save_panel(
            self.bot.database, guild_id=interaction.guild_id, channel_id=channel.id,
            message_id=message.id, kind=kind)
        await interaction.followup.send(
            embed=notice("Панель сохранена. Обновление каждые 10 минут.", kind="success"),
            ephemeral=True)

    @tasks.loop(minutes=10)
    async def panel_tick(self) -> None:
        if await self.enabled():
            await self.refresh_panels()
        await store.prune_receipts(self.bot.database)

    @panel_tick.before_loop
    async def before_panel_tick(self) -> None:
        await self.bot.wait_until_ready()

    async def refresh_panels(self) -> None:
        guild = self.bot.get_guild(self.bot.config.guild_id)
        if guild is None:
            return
        for panel in await store.panels(self.bot.database, guild.id):
            channel = guild.get_channel(panel.channel_id)
            if not isinstance(channel, discord.TextChannel):
                log.warning("Отсутствует канал панели Statistics id=%s", panel.channel_id)
                continue
            try:
                message = await channel.fetch_message(panel.message_id)
                embed = (await self.server_embed(guild, "7d") if panel.kind == "server"
                         else await self.activity_embed(guild, "7d"))
                await message.edit(embed=embed, allowed_mentions=NO_MENTIONS)
                await store.mark_panel_updated(
                    self.bot.database, panel_id=panel.id, guild_id=guild.id)
            except discord.NotFound:
                log.warning("Панель статистики удалена id=%s", panel.message_id)
                await store.remove_panel(self.bot.database, panel.id, guild.id)
            except discord.HTTPException:
                log.exception("Не обновлена Stats panel id=%s", panel.id)


async def setup(bot) -> None:
    await bot.add_cog(Statistics(bot), guild=discord.Object(id=bot.config.guild_id))
