"""Discord-интерфейс мероприятий и розыгрышей NewSquad."""
from __future__ import annotations

import json
import logging
import time

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select

from app.core.durations import parse_duration
from app.core.permissions import can_manage_settings
from app.models import MemberProgress, ModerationCase
from app.services import events as store
from app.services.audit import log_event
from app.services.membership_access import approved_member
from app.ui.theme import LoggedView, card, notice

log = logging.getLogger(__name__)
ALLOWED_MENTIONS = discord.AllowedMentions.none()


def event_embed(event) -> discord.Embed:
    names = {"scheduled": "Регистрация", "live": "Идёт", "finished": "Завершено",
             "cancelled": "Отменено"}
    info = (f"**Описание:** {discord.utils.escape_mentions(event.description)}\n"
            f"**Ведущий:** <@{event.host_id}>\n"
            f"**Начало:** <t:{event.starts_at}:F>\n"
            f"**Длительность:** {(event.ends_at - event.starts_at) // 60} мин.\n"
            f"**Регистрация до:** <t:{event.registration_ends_at}:R>\n"
            f"**Часовой пояс:** {event.timezone}")
    e = card(f"NewSquad / {discord.utils.escape_mentions(event.title)}", info)
    cap = str(event.capacity) if event.capacity else "∞"
    e.add_field(name="Места", value=f"{event.confirmed_count}/{cap}", inline=True)
    e.add_field(name="Очередь", value=str(event.waitlist_count), inline=True)
    e.add_field(name="Статус", value=names.get(event.status, event.status), inline=True)
    if event.reward_xp or event.reward_coins:
        e.add_field(name="Награды за подтверждённое участие",
                    value=f"{event.reward_xp} XP · {event.reward_coins} NS Coins", inline=False)
    e.set_footer(text=f"NewSquad • Мероприятие #{event.id:04d}")
    return e


def giveaway_embed(giveaway) -> discord.Embed:
    e = card(f"NewSquad / Розыгрыш #{giveaway.id:04d}",
        f"**Приз:** {discord.utils.escape_mentions(giveaway.prize)}\n"
        f"**Условия:** {discord.utils.escape_mentions(giveaway.conditions)}\n"
        f"**Окончание:** <t:{giveaway.ends_at}:F>\n"
        f"**Победителей:** {giveaway.winner_count}\n"
        f"**Минимальный уровень:** {giveaway.min_level or 'без требований'}\n"
        f"**Дополнительная роль:** {f'<@&{giveaway.required_role_id}>' if giveaway.required_role_id else 'не требуется'}")
    e.add_field(name="Заявок", value=str(giveaway.entry_count), inline=True)
    e.add_field(name="Статус", value="Идёт" if giveaway.status == "open" else "Завершён", inline=True)
    if giveaway.status == "ended":
        winners = json.loads(giveaway.winners_json)
        e.add_field(name=f"Победители · выбор #{giveaway.draw_round}",
                    value="\n".join(f"<@{uid}>" for uid in winners) or "Нет допущенных участников.",
                    inline=False)
    e.set_footer(text=f"NewSquad • Розыгрыш #{giveaway.id:04d} • Участие бесплатное")
    return e


class EventAction(discord.ui.Button):
    def __init__(self, cog: "Events", event_id: int, action: str):
        spec = {"join": ("Участвую", discord.ButtonStyle.success),
                "leave": ("Отменить участие", discord.ButtonStyle.secondary),
                "members": ("Участники", discord.ButtonStyle.primary)}
        label, style = spec[action]
        super().__init__(label=label, style=style, custom_id=f"newsqad:event:{event_id}:{action}")
        self.cog, self.event_id, self.action = cog, event_id, action

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.cog.event_action(interaction, self.event_id, self.action)


class EventView(LoggedView):
    def __init__(self, cog: "Events", event_id: int):
        super().__init__(timeout=None)
        for action in ("join", "leave", "members"):
            self.add_item(EventAction(cog, event_id, action))


class GiveawayAction(discord.ui.Button):
    def __init__(self, cog: "Events", giveaway_id: int):
        super().__init__(label="Участвовать", style=discord.ButtonStyle.success,
                         custom_id=f"newsqad:giveaway:{giveaway_id}:join")
        self.cog, self.giveaway_id = cog, giveaway_id

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.cog.giveaway_action(interaction, self.giveaway_id)


class GiveawayView(LoggedView):
    def __init__(self, cog: "Events", giveaway_id: int):
        super().__init__(timeout=None)
        self.add_item(GiveawayAction(cog, giveaway_id))


class Events(commands.Cog):
    event = app_commands.Group(name="event", description="Мероприятия NewSquad")
    giveaway = app_commands.Group(name="giveaway", description="Бесплатные розыгрыши NewSquad")

    def __init__(self, bot):
        self.bot = bot
        self._restored = False

    async def cog_load(self) -> None:
        self.bot.scheduler.register("events.remind", self._remind)
        self.bot.scheduler.register("events.start", self._start)
        self.bot.scheduler.register("events.finish", self._finish)
        self.bot.scheduler.register("events.giveaway_end", self._end_task)
        gid = self.bot.config.guild_id
        for e in await store.active_events(self.bot.database, gid):
            if e.message_id:
                self.bot.add_view(EventView(self, e.id), message_id=e.message_id)
        for g in await store.open_giveaways(self.bot.database, gid):
            if g.message_id:
                self.bot.add_view(GiveawayView(self, g.id), message_id=g.message_id)

    async def _enabled(self, key: str) -> bool:
        return bool(await self.bot.database.get_setting(self.bot.config.guild_id, key, True))

    def _admin(self, interaction: discord.Interaction) -> bool:
        return can_manage_settings(interaction, self.bot.config.owner_ids)

    async def _member(self, member: discord.Member) -> bool:
        if (not isinstance(member, discord.Member) or member.bot
                or member.guild.id != self.bot.config.guild_id):
            return False
        role_id = await self.bot.database.get_setting(member.guild.id, "member_role_id")
        if not await approved_member(self.bot.database, member, role_id):
            return False
        async with self.bot.database.sessions() as session:
            banned = await session.scalar(select(ModerationCase.id).where(
                ModerationCase.guild_id == member.guild.id, ModerationCase.user_id == member.id,
                ModerationCase.kind == "ban",
                ModerationCase.status.in_(("active", "external_active"))).limit(1))
        return banned is None

    async def _giveaway_eligible(self, member: discord.Member, giveaway) -> bool:
        if not await self._member(member):
            return False
        if giveaway.required_role_id and not any(
                role.id == giveaway.required_role_id for role in member.roles):
            return False
        if giveaway.min_level:
            async with self.bot.database.sessions() as session:
                level = await session.scalar(select(MemberProgress.level).where(
                    MemberProgress.guild_id == member.guild.id,
                    MemberProgress.user_id == member.id))
            if (level or 1) < giveaway.min_level:
                return False
        return True

    async def _schedule_event(self, e) -> None:
        for offset in store.reminder_offsets(e.reminders):
            await self.bot.scheduler.schedule(job_key=f"event:{e.id}:remind:{offset}",
                handler="events.remind", due_at=e.starts_at - offset * 60,
                payload={"id": e.id, "minutes": offset})
        await self.bot.scheduler.schedule(job_key=f"event:{e.id}:start",
            handler="events.start", due_at=e.starts_at, payload={"id": e.id})
        await self.bot.scheduler.schedule(job_key=f"event:{e.id}:finish",
            handler="events.finish", due_at=e.ends_at, payload={"id": e.id})

    async def _schedule_giveaway(self, g) -> None:
        await self.bot.scheduler.schedule(job_key=f"giveaway:{g.id}:end",
            handler="events.giveaway_end", due_at=g.ends_at, payload={"id": g.id})

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._restored:
            return
        self._restored = True
        gid = self.bot.config.guild_id
        for e in await store.active_events(self.bot.database, gid):
            try:
                await self._schedule_event(e)
                if not e.message_id:
                    await self._publish_event(e)
            except Exception:
                log.exception("Не восстановлено мероприятие event_id=%s", e.id)
        for g in await store.open_giveaways(self.bot.database, gid):
            try:
                await self._schedule_giveaway(g)
                if not g.message_id:
                    await self._publish_giveaway(g)
            except Exception:
                log.exception("Не восстановлен розыгрыш giveaway_id=%s", g.id)

    async def _send_notice(self, channel_id: int, message: str) -> None:
        guild = self.bot.get_guild(self.bot.config.guild_id)
        channel = guild.get_channel(channel_id) if guild else None
        if not isinstance(channel, discord.TextChannel):
            raise RuntimeError(f"Не найден канал для уведомления: {channel_id}")
        await channel.send(embed=card("NewSquad / Мероприятия", message),
                           allowed_mentions=ALLOWED_MENTIONS)

    async def _remind(self, payload: dict) -> None:
        event_id, minutes = int(payload["id"]), int(payload["minutes"])
        row = await store.reminder_state(self.bot.database, self.bot.config.guild_id, event_id, minutes)
        if not row or row[0] is not None:
            return
        _, starts_at, status, channel_id, title = row
        now = int(time.time())
        if status != "scheduled" or now >= starts_at:
            return
        await self._send_notice(channel_id,
            f"**{discord.utils.escape_mentions(title)}** начинается через {minutes} мин.\n"
            f"Начало: <t:{starts_at}:F>.")
        # При HTTP-ошибке задача повторяется планировщиком. Между Discord send
        # и записью sent_at возможен повтор после аварийного выключения.
        await store.mark_reminder_sent(self.bot.database, event_id, minutes)
        log.info("Отправлено напоминание event_id=%s minutes=%s", event_id, minutes)

    async def _start(self, payload: dict) -> None:
        event_id = int(payload["id"])
        if await store.mark_live(self.bot.database, self.bot.config.guild_id, event_id):
            log.info("Мероприятие началось event_id=%s", event_id)
        await self._refresh_event(event_id)

    async def _finish(self, payload: dict) -> None:
        event_id = int(payload["id"])
        if await store.mark_finished(self.bot.database, self.bot.config.guild_id, event_id):
            log.info("Мероприятие завершено event_id=%s", event_id)
        await self._refresh_event(event_id)

    async def _end_task(self, payload: dict) -> None:
        try:
            await self._draw(int(payload["id"]), actor_id=0, reason="Автоматическое завершение")
        except ValueError as exc:
            log.info("Розыгрыш не требует завершения: giveaway_id=%s причина=%s", payload["id"], exc)

    async def _publish_event(self, e) -> None:
        guild = self.bot.get_guild(e.guild_id)
        channel = guild.get_channel(e.channel_id) if guild else None
        if not isinstance(channel, discord.TextChannel):
            raise RuntimeError("Канал мероприятия не настроен или удалён.")
        if e.message_id:
            return
        message = await channel.send(embed=event_embed(e), view=EventView(self, e.id),
                                     allowed_mentions=ALLOWED_MENTIONS)
        await store.set_event_message(self.bot.database, e.guild_id, e.id, message.id)
        self.bot.add_view(EventView(self, e.id), message_id=message.id)

    async def _publish_giveaway(self, g) -> None:
        guild = self.bot.get_guild(g.guild_id)
        channel = guild.get_channel(g.channel_id) if guild else None
        if not isinstance(channel, discord.TextChannel):
            raise RuntimeError("Канал розыгрыша не настроен или удалён.")
        if g.message_id:
            return
        message = await channel.send(embed=giveaway_embed(g), view=GiveawayView(self, g.id),
                                     allowed_mentions=ALLOWED_MENTIONS)
        await store.set_giveaway_message(self.bot.database, g.guild_id, g.id, message.id)
        self.bot.add_view(GiveawayView(self, g.id), message_id=message.id)

    async def _refresh_event(self, event_id: int) -> None:
        e = await store.get_event(self.bot.database, self.bot.config.guild_id, event_id)
        if not e or not e.message_id:
            return
        guild = self.bot.get_guild(e.guild_id)
        channel = guild.get_channel(e.channel_id) if guild else None
        if not isinstance(channel, discord.TextChannel):
            log.warning("Карточка мероприятия недоступна event_id=%s", event_id)
            return
        try:
            msg = await channel.fetch_message(e.message_id)
            await msg.edit(embed=event_embed(e), view=EventView(self, e.id) if e.status == "scheduled" else None,
                           allowed_mentions=ALLOWED_MENTIONS)
        except discord.HTTPException:
            log.exception("Ошибка обновления мероприятия event_id=%s", event_id)

    async def _refresh_giveaway(self, giveaway_id: int) -> None:
        g = await store.get_giveaway(self.bot.database, self.bot.config.guild_id, giveaway_id)
        if not g or not g.message_id:
            return
        guild = self.bot.get_guild(g.guild_id)
        channel = guild.get_channel(g.channel_id) if guild else None
        if not isinstance(channel, discord.TextChannel):
            log.warning("Карточка розыгрыша недоступна giveaway_id=%s", giveaway_id)
            return
        try:
            msg = await channel.fetch_message(g.message_id)
            await msg.edit(embed=giveaway_embed(g), view=GiveawayView(self, g.id) if g.status == "open" else None,
                           allowed_mentions=ALLOWED_MENTIONS)
        except discord.HTTPException:
            log.exception("Ошибка обновления розыгрыша giveaway_id=%s", giveaway_id)

    async def event_action(self, interaction: discord.Interaction, event_id: int, action: str) -> None:
        if interaction.guild_id != self.bot.config.guild_id or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(embed=notice("Доступно только в NewSquad.", kind="warning"),
                                                    ephemeral=True)
            return
        if not await self._enabled("events_enabled") or not await self._member(interaction.user):
            await interaction.response.send_message(embed=notice("Только для одобренных участников.", kind="warning"),
                                                    ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            e = await store.get_event(self.bot.database, interaction.guild_id, event_id)
            if e is None:
                raise ValueError("Мероприятие не найдено.")
            if action == "join":
                status = await store.register(self.bot.database, interaction.guild_id, event_id, interaction.user.id)
                message = "Вы зарегистрированы." if status == "registered" else "Вы добавлены в очередь ожидания."
            elif action == "leave":
                promoted = await store.withdraw(self.bot.database, interaction.guild_id, event_id, interaction.user.id)
                message = "Регистрация отменена."
                if promoted is not None:
                    message += " Первый участник из очереди получил место."
            elif action == "members":
                rows = await store.event_signups(self.bot.database, event_id)
                confirmed = [r.user_id for r in rows if r.status == "registered"]
                waiting = [r.user_id for r in rows if r.status == "waitlist"]
                message = ("**Зарегистрированы:** " + (", ".join(f"<@{uid}>" for uid in confirmed[:30]) or "пока нет")
                    + f"\n**Всего:** {len(confirmed)} · **Ожидают:** {len(waiting)}"
                    + ("\n**Очередь:** " + ", ".join(f"<@{uid}>" for uid in waiting[:15]) if waiting else ""))
            else:
                raise ValueError("Неизвестное действие.")
        except ValueError as exc:
            await interaction.followup.send(embed=notice(str(exc), kind="warning"), ephemeral=True)
            return
        if action != "members":
            await self._refresh_event(event_id)
        await interaction.followup.send(embed=notice(message, kind="success"), ephemeral=True,
                                        allowed_mentions=ALLOWED_MENTIONS)

    async def giveaway_action(self, interaction: discord.Interaction, giveaway_id: int) -> None:
        if interaction.guild_id != self.bot.config.guild_id or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(embed=notice("Только на NewSquad.", kind="warning"),
                                                    ephemeral=True)
            return
        if not await self._enabled("giveaways_enabled"):
            await interaction.response.send_message(embed=notice("Розыгрыши отключены.", kind="warning"),
                                                    ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        g = await store.get_giveaway(self.bot.database, interaction.guild_id, giveaway_id)
        if g is None or not await self._giveaway_eligible(interaction.user, g):
            await interaction.followup.send(embed=notice("Вы не подходите под условия участия.", kind="warning"),
                                            ephemeral=True)
            return
        try:
            await store.enter_giveaway(self.bot.database, interaction.guild_id, giveaway_id, interaction.user.id)
        except ValueError as exc:
            await interaction.followup.send(embed=notice(str(exc), kind="warning"), ephemeral=True)
            return
        await self._refresh_giveaway(giveaway_id)
        await interaction.followup.send(embed=notice("Вы участвуете в розыгрыше.", kind="success"),
                                        ephemeral=True)

    async def _draw(self, giveaway_id: int, *, actor_id: int, reason: str,
                    force: bool = False) -> list[int]:
        g = await store.get_giveaway(self.bot.database, self.bot.config.guild_id, giveaway_id)
        if g is None or g.status != "open":
            raise ValueError("Розыгрыш уже завершён или не найден.")
        guild = self.bot.get_guild(g.guild_id)
        if guild is None:
            raise RuntimeError("Сервер NewSquad пока недоступен.")
        entrants = await store.giveaway_entrants(self.bot.database, giveaway_id)
        eligible = []
        for user_id in entrants:
            member = guild.get_member(user_id)
            if member is None:
                try:
                    member = await guild.fetch_member(user_id)
                except discord.NotFound:
                    continue
                # Прочие HTTP ошибки не скрываем: планировщик повторит проверку.
            if await self._giveaway_eligible(member, g):
                eligible.append(user_id)
        winners = await store.draw_giveaway(self.bot.database, guild_id=g.guild_id, giveaway_id=g.id,
            eligible_ids=eligible, actor_id=actor_id, reason=reason, force=force)
        await self._refresh_giveaway(g.id)
        try:
            await self._send_notice(g.channel_id,
                f"Розыгрыш **#{g.id:04d}** завершён. Победители: "
                + (", ".join(f"<@{uid}>" for uid in winners) if winners else "нет допущенных заявок."))
        except (discord.HTTPException, RuntimeError):
            log.exception("Не опубликованы итоги giveaway_id=%s", g.id)
        log.info("Розыгрыш завершён giveaway_id=%s eligible=%s winners=%s", g.id, len(eligible), winners)
        return winners

    @event.command(name="create", description="Создать мероприятие с регистрацией и напоминаниями")
    async def create_event(self, interaction: discord.Interaction, title: str, description: str,
                           starts: str, channel: discord.TextChannel, host: discord.Member,
                           duration_minutes: app_commands.Range[int, 15, 1440] = 120,
                           capacity: app_commands.Range[int, 0, 1000] = 0,
                           registration_closes_minutes: app_commands.Range[int, 0, 10080] = 0,
                           reminders: str = "60,15", timezone_name: str | None = None,
                           waitlist: bool = True,
                           reward_xp: app_commands.Range[int, 0, 1000] = 0,
                           reward_coins: app_commands.Range[int, 0, 1000] = 0) -> None:
        if not self._admin(interaction) or channel.guild.id != self.bot.config.guild_id or host.guild.id != self.bot.config.guild_id:
            await interaction.response.send_message(embed=notice("Только администрация NewSquad.", kind="warning"),
                                                    ephemeral=True)
            return
        if not await self._enabled("events_enabled"):
            await interaction.response.send_message(embed=notice("Мероприятия отключены.", kind="warning"),
                                                    ephemeral=True)
            return
        try:
            timezone_name = timezone_name or self.bot.config.timezone
            start_ts = store.local_to_utc(starts, timezone_name)
            # Проверяем параметры до долгого Discord-ответа.
            store.reminder_offsets(reminders)
            if not 15 <= duration_minutes <= 1440:
                raise ValueError("Некорректная длительность.")
        except ValueError as exc:
            await interaction.response.send_message(embed=notice(str(exc), kind="warning"), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            e = await store.create_event(self.bot.database, guild_id=interaction.guild_id,
                creator_id=interaction.user.id, host_id=host.id, channel_id=channel.id,
                title=title, description=description, starts_at=start_ts, timezone_name=timezone_name,
                duration_minutes=duration_minutes, capacity=capacity, waitlist_enabled=waitlist,
                registration_closes_minutes=registration_closes_minutes, reminders=reminders,
                reward_xp=reward_xp, reward_coins=reward_coins)
            await self._schedule_event(e)
            try:
                await self._publish_event(e)
                message = f"Мероприятие #{e.id:04d} опубликовано."
            except (discord.HTTPException, RuntimeError):
                log.exception("Не опубликовано мероприятие event_id=%s", e.id)
                message = f"Мероприятие #{e.id:04d} сохранено; публикация будет повторена после перезапуска."
            await log_event(self.bot, channel_setting="audit_channel_id", title="Мероприятие создано",
                details=f"ID: {e.id}; начало UTC: {e.starts_at}; ведущий: {e.host_id}",
                action="events.created", actor_id=interaction.user.id, target_id=e.id)
            await interaction.followup.send(embed=notice(message, kind="success"), ephemeral=True)
        except ValueError as exc:
            await interaction.followup.send(embed=notice(str(exc), kind="warning"), ephemeral=True)

    @event.command(name="list", description="Показать мероприятия NewSquad")
    async def list_event(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id != self.bot.config.guild_id or not await self._member(interaction.user):
            await interaction.response.send_message(embed=notice("Только для участников.", kind="warning"),
                                                    ephemeral=True)
            return
        rows = await store.list_events(self.bot.database, interaction.guild_id)
        lines = [f"**#{r.id:04d}** · {discord.utils.escape_mentions(r.title)} · "
                 f"<t:{r.starts_at}:R> · {r.confirmed_count} записались · {r.status}" for r in rows]
        await interaction.response.send_message(embed=card("NewSquad / Мероприятия",
            "\n".join(lines) or "Мероприятий пока нет."), ephemeral=True, allowed_mentions=ALLOWED_MENTIONS)

    @event.command(name="join", description="Зарегистрироваться на мероприятие")
    async def event_join(self, interaction: discord.Interaction, number: int) -> None:
        await self.event_action(interaction, number, "join")

    @event.command(name="leave", description="Отменить регистрацию")
    async def event_leave(self, interaction: discord.Interaction, number: int) -> None:
        await self.event_action(interaction, number, "leave")

    @event.command(name="participants", description="Посмотреть зарегистрированных")
    async def event_participants(self, interaction: discord.Interaction, number: int) -> None:
        await self.event_action(interaction, number, "members")

    @event.command(name="cancel", description="Отменить мероприятие")
    async def cancel_event(self, interaction: discord.Interaction, number: int, reason: str) -> None:
        if not self._admin(interaction):
            await interaction.response.send_message(embed=notice("Только администрация.", kind="warning"),
                                                    ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        reason = store.clean_text(reason, 400)
        if not await store.cancel_event(self.bot.database, interaction.guild_id, number):
            await interaction.followup.send(embed=notice("Мероприятие уже закрыто или отсутствует.",
                                                        kind="warning"), ephemeral=True)
            return
        await self._refresh_event(number)
        await log_event(self.bot, channel_setting="audit_channel_id", title="Мероприятие отменено",
            details=f"Мероприятие: {number}; причина: {reason}", action="events.cancelled",
            actor_id=interaction.user.id, target_id=number)
        await interaction.followup.send(embed=notice("Мероприятие отменено.", kind="success"), ephemeral=True)

    @event.command(name="confirm", description="Подтвердить фактическое участие и выдать награду")
    async def event_confirm(self, interaction: discord.Interaction, number: int, member: discord.Member) -> None:
        if not self._admin(interaction) or member.guild.id != self.bot.config.guild_id:
            await interaction.response.send_message(embed=notice("Только администрация.", kind="warning"),
                                                    ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            xp, coins = await store.confirm_attendance(self.bot.database, guild_id=interaction.guild_id,
                event_id=number, user_id=member.id, actor_id=interaction.user.id,
                timezone_name=self.bot.config.timezone)
        except ValueError as exc:
            await interaction.followup.send(embed=notice(str(exc), kind="warning"), ephemeral=True)
            return
        await log_event(self.bot, channel_setting="audit_channel_id", title="Участие подтверждено",
            details=f"Мероприятие: {number}; пользователь: {member.id}; XP: {xp}; NS Coins: {coins}",
            actor_id=interaction.user.id, action="events.attendance_confirmed", target_id=member.id)
        await interaction.followup.send(embed=notice(
            f"Участие подтверждено. Начислено {xp} XP и {coins} NS Coins.", kind="success"), ephemeral=True)

    @event.command(name="settings", description="Проверить настройки мероприятий")
    async def event_settings(self, interaction: discord.Interaction) -> None:
        if not self._admin(interaction):
            await interaction.response.send_message(embed=notice("Только администрация.", kind="warning"),
                                                    ephemeral=True)
            return
        enabled = await self._enabled("events_enabled")
        g_enabled = await self._enabled("giveaways_enabled")
        await interaction.response.send_message(embed=card("NewSquad / Events",
            f"**Мероприятия:** {'включены' if enabled else 'выключены'}\n"
            f"**Розыгрыши:** {'включены' if g_enabled else 'выключены'}\n"
            f"**Часовой пояс по умолчанию:** {self.bot.config.timezone}\n"
            "Начало: ГГГГ-ММ-ДД ЧЧ:ММ (местное время заданного пояса).\n"
            "Напоминания: минуты через запятую (например 60,15).\n"
            "Награды выдаются только через /event confirm."), ephemeral=True)

    @event.command(name="configure", description="Включить или отключить мероприятия и розыгрыши")
    async def event_configure(self, interaction: discord.Interaction, events_enabled: bool,
                              giveaways_enabled: bool) -> None:
        if not self._admin(interaction):
            await interaction.response.send_message(embed=notice("Только администрация.", kind="warning"),
                                                    ephemeral=True)
            return
        for key, value in (("events_enabled", events_enabled), ("giveaways_enabled", giveaways_enabled)):
            await self.bot.database.set_setting(interaction.guild_id, key, value, actor_id=interaction.user.id)
        await interaction.response.send_message(embed=notice("Настройки сохранены.", kind="success"), ephemeral=True)

    @giveaway.command(name="create", description="Открыть бесплатный розыгрыш")
    async def create_giveaway(self, interaction: discord.Interaction, prize: str, conditions: str,
                              channel: discord.TextChannel, duration: str = "24h",
                              winners: app_commands.Range[int, 1, 20] = 1,
                              required_role: discord.Role | None = None,
                              min_level: app_commands.Range[int, 0, 1000] = 0) -> None:
        if not self._admin(interaction) or channel.guild.id != self.bot.config.guild_id:
            await interaction.response.send_message(embed=notice("Только администрация.", kind="warning"),
                                                    ephemeral=True)
            return
        if not await self._enabled("giveaways_enabled"):
            await interaction.response.send_message(embed=notice("Розыгрыши выключены.", kind="warning"),
                                                    ephemeral=True)
            return
        if required_role and (required_role.guild.id != self.bot.config.guild_id
                              or required_role.permissions.administrator):
            await interaction.response.send_message(embed=notice("Выберите обычную роль NewSquad.", kind="warning"),
                                                    ephemeral=True)
            return
        try:
            seconds = parse_duration(duration, allow_permanent=False, max_seconds=30 * 86400)
            if seconds < 60:
                raise ValueError("Минимальная длительность: одна минута.")
        except ValueError as exc:
            await interaction.response.send_message(embed=notice(str(exc), kind="warning"), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            g = await store.create_giveaway(self.bot.database, guild_id=interaction.guild_id,
                creator_id=interaction.user.id, channel_id=channel.id, prize=prize, conditions=conditions,
                ends_at=int(time.time()) + seconds, winner_count=winners,
                required_role_id=required_role.id if required_role else None, min_level=min_level)
            await self._schedule_giveaway(g)
            try:
                await self._publish_giveaway(g)
                msg = f"Розыгрыш #{g.id:04d} опубликован."
            except (discord.HTTPException, RuntimeError):
                log.exception("Не опубликован розыгрыш giveaway_id=%s", g.id)
                msg = f"Розыгрыш #{g.id:04d} сохранён; публикация будет повторена после перезапуска."
            await log_event(self.bot, channel_setting="audit_channel_id", title="Розыгрыш создан",
                details=f"ID: {g.id}; приз: {g.prize}; до UTC: {g.ends_at}", action="events.giveaway_created",
                actor_id=interaction.user.id, target_id=g.id)
            await interaction.followup.send(embed=notice(msg, kind="success"), ephemeral=True)
        except ValueError as exc:
            await interaction.followup.send(embed=notice(str(exc), kind="warning"), ephemeral=True)

    @giveaway.command(name="join", description="Участвовать в бесплатном розыгрыше")
    async def giveaway_join(self, interaction: discord.Interaction, number: int) -> None:
        await self.giveaway_action(interaction, number)

    @giveaway.command(name="list", description="Посмотреть розыгрыши")
    async def giveaway_list(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id != self.bot.config.guild_id or not await self._member(interaction.user):
            await interaction.response.send_message(embed=notice("Только для участников.", kind="warning"),
                                                    ephemeral=True)
            return
        rows = await store.list_giveaways(self.bot.database, interaction.guild_id)
        lines = [f"**#{r.id:04d}** · {discord.utils.escape_mentions(r.prize)} · "
                 f"{r.entry_count} заявок · {r.status}" for r in rows]
        await interaction.response.send_message(embed=card("NewSquad / Розыгрыши",
            "\n".join(lines) or "Розыгрышей пока нет."), ephemeral=True, allowed_mentions=ALLOWED_MENTIONS)

    @giveaway.command(name="end", description="Досрочно подвести итоги розыгрыша")
    async def giveaway_end(self, interaction: discord.Interaction, number: int) -> None:
        if not self._admin(interaction):
            await interaction.response.send_message(embed=notice("Только администрация.", kind="warning"),
                                                    ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            winners = await self._draw(number, actor_id=interaction.user.id,
                                       reason="Досрочное завершение администратором", force=True)
        except ValueError as exc:
            await interaction.followup.send(embed=notice(str(exc), kind="warning"), ephemeral=True)
            return
        await log_event(self.bot, channel_setting="audit_channel_id", title="Итоги розыгрыша",
            details=f"Розыгрыш: {number}; победители: {winners}", actor_id=interaction.user.id,
            action="events.giveaway_ended", target_id=number)
        await interaction.followup.send(embed=notice("Результаты сохранены в истории.", kind="success"),
                                        ephemeral=True)

    @giveaway.command(name="reroll", description="Повторный выбор с обязательной причиной")
    async def giveaway_reroll(self, interaction: discord.Interaction, number: int, reason: str) -> None:
        if not self._admin(interaction):
            await interaction.response.send_message(embed=notice("Только администрация.", kind="warning"),
                                                    ephemeral=True)
            return
        try:
            reason = store.clean_text(reason, 400)
        except ValueError as exc:
            await interaction.response.send_message(embed=notice(str(exc), kind="warning"),
                                                    ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            winners = await store.reroll_giveaway(self.bot.database, guild_id=interaction.guild_id,
                giveaway_id=number, actor_id=interaction.user.id, reason=reason)
        except ValueError as exc:
            await interaction.followup.send(embed=notice(str(exc), kind="warning"), ephemeral=True)
            return
        await self._refresh_giveaway(number)
        await log_event(self.bot, channel_setting="audit_channel_id", title="Повторный выбор победителей",
            details=f"Розыгрыш: {number}; новые победители: {winners}; причина: {reason}",
            actor_id=interaction.user.id, action="events.giveaway_reroll", target_id=number)
        await interaction.followup.send(embed=notice(
            "Повторный выбор сохранён вместе с прежним результатом и причиной.", kind="success"), ephemeral=True)

    @giveaway.command(name="history", description="История выборов победителей")
    async def giveaway_history(self, interaction: discord.Interaction, number: int) -> None:
        if not self._admin(interaction):
            await interaction.response.send_message(embed=notice("Только администрация.", kind="warning"),
                                                    ephemeral=True)
            return
        g = await store.get_giveaway(self.bot.database, interaction.guild_id, number)
        if g is None:
            await interaction.response.send_message(embed=notice("Розыгрыш не найден.", kind="warning"),
                                                    ephemeral=True)
            return
        entries = await store.giveaway_draws(self.bot.database, number)
        lines = [f"**Выбор #{d.round}** · <t:{d.created_at}:f> · {d.reason}\n"
                 f"Победители: {', '.join(f'<@{u}>' for u in json.loads(d.winners_json)) or 'нет'}"
                 for d in entries[-10:]]
        await interaction.response.send_message(embed=card("NewSquad / История розыгрыша",
            "\n".join(lines)[:3700] or "Итоги ещё не подведены."), ephemeral=True,
            allowed_mentions=ALLOWED_MENTIONS)


async def setup(bot) -> None:
    await bot.add_cog(Events(bot), guild=discord.Object(id=bot.config.guild_id))
