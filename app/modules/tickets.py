"""Приватные обращения NewSquad: панели, категории, назначение и архивирование."""
from __future__ import annotations

import asyncio
import logging
import time

import discord
from discord import app_commands
from discord.ext import commands

from app.core.permissions import can_manage_settings
from app.models import Ticket
from app.modules.tickets_views import (
    ConfirmTicketDeletion as ConfirmTicketDeletion,
    NewTicketModal as NewTicketModal,
    SupportSurveyView as SupportSurveyView,
    TicketActions as TicketActions,
    TicketCategorySelect as TicketCategorySelect,
    TicketCategoryView as TicketCategoryView,
    TicketOpenView as TicketOpenView,
    report_ticket_ui_error as report_ticket_ui_error,
    ticket_embed as ticket_embed,
)
from app.services import support_quality, support_sla, tickets
from app.services.membership_access import approved_member
from app.ui.theme import BRAND_BLUE, card, notice, panel_card

log = logging.getLogger(__name__)


class Tickets(commands.Cog):
    tickets = app_commands.Group(name="tickets", description="Обращения в поддержку NewSquad")
    categories_group = app_commands.Group(name="category", description="Категории обращений", parent=tickets)

    def __init__(self, bot):
        self.bot = bot
        self._member_locks: dict[int, asyncio.Lock] = {}
        self._loaded = False
        self._last_message: dict[int, int] = {}
        self._survey_task: asyncio.Task | None = None
        bot.scheduler.register("tickets.inactive_warn", self._warn_inactive)
        bot.scheduler.register("tickets.inactive_close", self._close_inactive)
        bot.scheduler.register("tickets.first_reply_alert", self._alert_unanswered)
        bot.scheduler.register("tickets.sla_idle_alert", self._alert_sla_idle)

    async def cog_load(self) -> None:
        await tickets.seed_categories(self.bot.database, self.bot.config.guild_id)
        self.bot.add_view(TicketOpenView(self))
        active = await tickets.list_tickets(self.bot.database, self.bot.config.guild_id, limit=1000)
        for ticket in active:
            if ticket.channel_id and ticket.status == "open" and ticket.control_message_id:
                self.bot.add_view(TicketActions(self, ticket.id), message_id=ticket.control_message_id)
        await support_quality.purge(self.bot.database, self.bot.config.guild_id)
        for survey in await support_quality.pending(self.bot.database, self.bot.config.guild_id):
            if survey.notified_at is not None:
                self.bot.add_view(SupportSurveyView(self.bot, survey.id, survey.requester_id))
        self._survey_task = asyncio.create_task(
            self._survey_maintenance(), name="support-survey-maintenance")

    async def cog_unload(self) -> None:
        if self._survey_task:
            self._survey_task.cancel()
            await asyncio.gather(self._survey_task, return_exceptions=True)
            self._survey_task = None

    async def _survey_maintenance(self) -> None:
        while True:
            await asyncio.sleep(3600)
            try:
                await support_quality.purge(self.bot.database, self.bot.config.guild_id)
                await self._deliver_surveys()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Не удалось очистить или доставить опросы поддержки")

    async def staff(self, interaction: discord.Interaction, ticket: Ticket | None = None) -> bool:
        if interaction.guild_id != self.bot.config.guild_id or not isinstance(interaction.user, discord.Member):
            return False
        if can_manage_settings(interaction, self.bot.config.owner_ids):
            return True
        if ticket:
            return any(role.id == ticket.staff_role_id for role in interaction.user.roles)
        support_role = await self.bot.database.get_setting(interaction.guild_id, "support_role_id")
        return bool(support_role and any(r.id == support_role for r in interaction.user.roles))

    async def require_admin(self, interaction: discord.Interaction) -> bool:
        if can_manage_settings(interaction, self.bot.config.owner_ids):
            return True
        await interaction.response.send_message("Эта настройка доступна только администрации.", ephemeral=True)
        return False

    async def audit(self, title: str, ticket_id: int, actor_id: int, details: str = "") -> None:
        """Тикеты не пишутся в AuditEntry/Discord-журналы по требованиям приватности."""
        return None

    async def get_channel(self, ticket: Ticket) -> discord.TextChannel | None:
        guild = self.bot.get_guild(self.bot.config.guild_id)
        if not guild or not ticket.channel_id:
            return None
        channel = guild.get_channel(ticket.channel_id)
        if isinstance(channel, discord.TextChannel):
            return channel
        try:
            channel = await guild.fetch_channel(ticket.channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None
        return channel if isinstance(channel, discord.TextChannel) else None

    async def create(self, interaction: discord.Interaction, category_id: int, subject: str, description: str) -> str:
        guild = interaction.guild
        if guild is None or guild.id != self.bot.config.guild_id or not isinstance(interaction.user, discord.Member):
            return "Обращения доступны только на NewSquad."
        if not await self.bot.database.get_setting(guild.id, "tickets_enabled", True):
            return "Поддержка сейчас отключена администрацией."
        category = await tickets.category(self.bot.database, guild.id, category_id)
        if category is None or not category.enabled:
            return "Эта категория обращений больше не доступна."
        member_role = await self.bot.database.get_setting(guild.id, "candidate_role_id")
        if not await approved_member(self.bot.database, interaction.user, member_role) and not category.candidate_allowed:
            return "Эта категория доступна только одобренным участникам NewSquad."
        support_id = category.support_role_id or await self.bot.database.get_setting(guild.id, "support_role_id")
        support_role = guild.get_role(support_id) if support_id else None
        parent_id = await self.bot.database.get_setting(guild.id, "ticket_category_channel_id")
        parent = guild.get_channel(parent_id) if parent_id else None
        me = guild.me
        if not isinstance(parent, discord.CategoryChannel):
            return ("Категория Discord для тикетов не настроена или удалена. "
                    "Администрации нужно выбрать её в `/botsettings` → «Тикеты» → "
                    "«Категория каналов» либо через `/tickets location`.")
        if not support_role:
            return "Администрация ещё не назначила роль поддержки для этой категории."
        if me is None or not (me.guild_permissions.manage_channels and me.guild_permissions.manage_roles
                              and me.guild_permissions.send_messages):
            return "У бота отсутствуют права Manage Channels, Manage Roles или Send Messages."
        if support_role.is_default():
            return "Роль поддержки не может быть @everyone."
        lock = self._member_locks.setdefault(interaction.user.id, asyncio.Lock())
        async with lock:
            max_open = int(await self.bot.database.get_setting(guild.id, "tickets_max_open", 3))
            try:
                entry = await tickets.reserve(self.bot.database, guild.id, interaction.user.id,
                                            category.id, subject, description, support_role_id=support_role.id, max_open=max_open)
            except ValueError as exc:
                return str(exc)
            channel = None
            try:
                overwrites = {
                    guild.default_role: discord.PermissionOverwrite(view_channel=False),
                    interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True,
                                                                    read_message_history=True, attach_files=True),
                    me: discord.PermissionOverwrite(view_channel=True, send_messages=True,
                                                     read_message_history=True, manage_channels=True),
                    support_role: discord.PermissionOverwrite(view_channel=True, send_messages=True,
                                                              read_message_history=True),
                }
                channel = await guild.create_text_channel(f"ticket-{entry.id:05d}", category=parent,
                    overwrites=overwrites, reason=f"[NSB-TICKET-{entry.id}] Приватное обращение")
                message = await channel.send(embed=ticket_embed(entry, category),
                    view=TicketActions(self, entry.id), allowed_mentions=discord.AllowedMentions.none())
                await tickets.save_control_message(self.bot.database, guild.id, entry.id, message.id)
                if not await tickets.created(self.bot.database, guild.id, entry.id, channel.id):
                    raise RuntimeError("Тикет уже был обработан параллельно")
                try:
                    await self._schedule_inactivity(entry.id)
                    await self._schedule_first_reply_alert(entry.id)
                    await self._schedule_sla_idle(entry.id)
                    self.bot.add_view(TicketActions(self, entry.id), message_id=message.id)
                except Exception:
                    log.exception("Кнопки тикета #%s восстановятся при следующем запуске", entry.id)
                try:
                    await self.refresh(entry.id)
                    await self.audit("Создан тикет", entry.id, interaction.user.id, f"Категория: {category.key}")
                except Exception:
                    log.exception("Не удалось обновить карточку или аудит тикета %s", entry.id)
                return f"Обращение **#{entry.id:05d}** создано: {channel.mention}"
            except (discord.HTTPException, RuntimeError) as exc:
                log.exception("Создание приватного канала тикета %s не завершено", entry.id)
                if channel is not None:
                    try:
                        await channel.delete(reason=f"[NSB-TICKET-{entry.id}] Отмена неполного создания")
                    except discord.HTTPException:
                        log.exception("Не удалось удалить незавершённый тикет %s", entry.id)
                await tickets.fail_create(self.bot.database, guild.id, entry.id, type(exc).__name__)
                return "Не удалось открыть обращение. Администрация получила запись об ошибке; повторите попытку."

    async def refresh(self, ticket_id: int) -> None:
        ticket = await tickets.get(self.bot.database, self.bot.config.guild_id, ticket_id)
        if not ticket or not ticket.channel_id or not ticket.control_message_id:
            return
        channel = await self.get_channel(ticket)
        if channel is None:
            return
        try:
            msg = await channel.fetch_message(ticket.control_message_id)
            await msg.edit(embed=ticket_embed(ticket, await tickets.category(self.bot.database,
                ticket.guild_id, ticket.category_id)), view=TicketActions(self, ticket.id)
                if ticket.status == "open" else None, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            log.warning("Сообщение управления тикетом #%s недоступно", ticket_id)

    async def _deliver_surveys(self, *, requester_id: int | None = None) -> None:
        rows = await support_quality.pending(
            self.bot.database, self.bot.config.guild_id,
            requester_id=requester_id, unsent_only=True, limit=50)
        for survey in rows:
            try:
                user = self.bot.get_user(survey.requester_id)
                if user is None:
                    user = await self.bot.fetch_user(survey.requester_id)
                view = SupportSurveyView(self.bot, survey.id, survey.requester_id)
                await user.send(
                    "Ваше обращение в NewSqad закрыто. По желанию оцените работу поддержки "
                    "от 1 до 5 звёзд. Опрос доступен 7 дней. "
                    "Переписка и содержание тикета не сохраняются.",
                    view=view, allowed_mentions=discord.AllowedMentions.none())
                await support_quality.mark_sent(self.bot.database, self.bot.config.guild_id,
                                                survey.id)
            except discord.Forbidden:
                log.info("Не удалось отправить опрос после закрытия тикета (закрытые ЛС)")
            except discord.HTTPException:
                log.warning("Discord временно не доставил опрос %s", survey.id)
            except Exception:
                log.exception("Не удалось отправить оценку поддержки %s", survey.id)

    async def _delete_ticket(self, ticket: Ticket) -> str:
        """Удалить канал, затем все данные обращения; не писать в аудит.

        Если канал уже исчез, удаляем остатки БД. После сбоя удаления канала
        тикет останется в closing и восстановится при следующем запуске.
        """
        channel = await self.get_channel(ticket)
        if channel is not None:
            try:
                await channel.delete(reason="Закрытие приватного обращения NewSquad")
            except discord.NotFound:
                pass
            except discord.HTTPException:
                log.exception("Не удалось удалить канал тикета ticket_id=%s channel_id=%s",
                              ticket.id, ticket.channel_id)
                return "Discord не разрешил удалить канал. Проверьте Manage Channels и повторите закрытие."
        await tickets.erase_ticket(self.bot.database, ticket.guild_id, ticket.id)
        # Даже если чтение/доставка опроса ошибочно, закрытие уже завершено.
        try:
            await self._deliver_surveys(requester_id=ticket.user_id)
        except Exception:
            log.exception("Опрос недоступен после успешного удаления приватного тикета")
        return "Обращение закрыто. Канал удалён, переписка и история не сохранены."

    async def close_ticket(self, interaction: discord.Interaction, ticket_id: int, reason: str = "") -> str:
        ticket = await tickets.get(self.bot.database, self.bot.config.guild_id, ticket_id)
        if not ticket or interaction.guild_id != ticket.guild_id:
            return "Обращение не найдено или уже закрыто."
        if interaction.user.id != ticket.user_id and not await self.staff(interaction, ticket):
            return "Недостаточно прав на закрытие."
        if not await tickets.start_close(self.bot.database, ticket.guild_id, ticket_id,
                                       interaction.user.id, "Закрыто"):
            return "Обращение уже закрывается."
        try:
            return await self._delete_ticket(ticket)
        except Exception:
            log.exception("Ошибка полного удаления тикета ticket_id=%s", ticket_id)
            return "Удаление не завершено. Бот повторит операцию при следующем запуске."


    async def _schedule_first_reply_alert(self, ticket_id: int) -> None:
        ticket = await tickets.get(self.bot.database, self.bot.config.guild_id, ticket_id)
        if ticket is None or ticket.status != "open" or ticket.first_response_at is not None:
            return
        rules = await support_sla.get_rules(self.bot.database, ticket.guild_id)
        minutes = rules[ticket.priority]["first_minutes"]
        await self.bot.scheduler.schedule(
            job_key=f"tickets:sla:first:{ticket.id}:{ticket.priority}:{minutes}",
            handler="tickets.first_reply_alert",
            due_at=ticket.opened_at + minutes * 60,
            payload={"ticket_id": ticket.id, "priority": ticket.priority, "minutes": minutes})

    async def _alert_unanswered(self, payload: dict) -> None:
        """A scheduled notice is discarded when the priority/rules have changed."""
        ticket = await tickets.get(self.bot.database, self.bot.config.guild_id, int(payload["ticket_id"]))
        if ticket is None or ticket.status != "open" or ticket.first_response_at is not None:
            return
        if "priority" in payload:
            rules = await support_sla.get_rules(self.bot.database, ticket.guild_id)
            if payload["priority"] != ticket.priority or payload["minutes"] != rules[ticket.priority]["first_minutes"]:
                await self._schedule_first_reply_alert(ticket.id)
                return
            if time.time() < ticket.opened_at + rules[ticket.priority]["first_minutes"] * 60:
                return
        channel = await self.get_channel(ticket)
        if channel is None:
            raise RuntimeError("Канал тикета недоступен для уведомления SLA")
        await channel.send(embed=notice(
            f"SLA: обращение #{ticket.id:05d} ({ticket.priority}) ожидает первого ответа.", kind="warning"),
            allowed_mentions=discord.AllowedMentions.none())
        if ticket.assignee_id:
            try:
                user = self.bot.get_user(ticket.assignee_id) or await self.bot.fetch_user(ticket.assignee_id)
                await user.send(embed=notice(
                    f"SLA: обращение #{ticket.id:05d} ожидает ответа: {channel.jump_url}", kind="warning"),
                    allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException:
                log.warning("Не удалось уведомить ответственного за тикет #%s", ticket.id)

    async def _schedule_sla_idle(self, ticket_id: int) -> None:
        ticket = await tickets.get(self.bot.database, self.bot.config.guild_id, ticket_id)
        if ticket is None or ticket.status != "open":
            return
        rules = await support_sla.get_rules(self.bot.database, ticket.guild_id)
        minutes = rules[ticket.priority]["idle_minutes"]
        await self.bot.scheduler.schedule(
            job_key=f"tickets:sla:idle:{ticket.id}:{ticket.updated_at}:{ticket.priority}:{minutes}",
            handler="tickets.sla_idle_alert",
            due_at=ticket.updated_at + minutes * 60,
            payload={"ticket_id": ticket.id, "activity_at": ticket.updated_at,
                     "priority": ticket.priority, "minutes": minutes})

    async def _alert_sla_idle(self, payload: dict) -> None:
        ticket = await tickets.get(self.bot.database, self.bot.config.guild_id, int(payload["ticket_id"]))
        if ticket is None or ticket.status != "open":
            return
        rules = await support_sla.get_rules(self.bot.database, ticket.guild_id)
        minutes = rules[ticket.priority]["idle_minutes"]
        if (ticket.updated_at != int(payload["activity_at"]) or
                ticket.priority != payload["priority"] or minutes != payload["minutes"]):
            await self._schedule_sla_idle(ticket.id)
            return
        if time.time() < ticket.updated_at + minutes * 60:
            return
        channel = await self.get_channel(ticket)
        if channel is None:
            raise RuntimeError("Канал тикета недоступен для SLA inactivity")
        await channel.send(embed=notice(
            f"SLA: обращение #{ticket.id:05d} ({ticket.priority}) без активности.", kind="warning"),
            allowed_mentions=discord.AllowedMentions.none())
        if ticket.assignee_id:
            try:
                recipient = self.bot.get_user(ticket.assignee_id) or await self.bot.fetch_user(ticket.assignee_id)
                await recipient.send(
                    f"NewSqad SLA: нет активности по обращению #{ticket.id:05d}. {channel.jump_url}",
                    allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException:
                log.warning("Не удалось уведомить ответственного SLA idle #%s", ticket.id)

    async def _schedule_inactivity(self, ticket_id: int) -> None:
        days = int(await self.bot.database.get_setting(self.bot.config.guild_id, "tickets_inactivity_days", 0))
        if days == 0:
            return
        ticket = await tickets.get(self.bot.database, self.bot.config.guild_id, ticket_id)
        if ticket is None or ticket.status != "open":
            return
        due_at = max(int(time.time()), ticket.updated_at + days * 86400 - 86400)
        await self.bot.scheduler.schedule(job_key=f"tickets.warn.{ticket.id}.{ticket.updated_at}",
            handler="tickets.inactive_warn", due_at=due_at,
            payload={"ticket_id": ticket.id, "activity_at": ticket.updated_at, "days": days})

    async def _warn_inactive(self, payload: dict) -> None:
        ticket_id = int(payload["ticket_id"])
        ticket = await tickets.get(self.bot.database, self.bot.config.guild_id, ticket_id)
        days = int(await self.bot.database.get_setting(self.bot.config.guild_id, "tickets_inactivity_days", 0))
        if ticket is None or ticket.status != "open" or days == 0:
            return
        if days != int(payload["days"]) or ticket.updated_at != int(payload["activity_at"]):
            await self._schedule_inactivity(ticket.id)
            return
        channel = await self.get_channel(ticket)
        if channel is None:
            raise RuntimeError(f"Канал тикета #{ticket.id} недоступен для уведомления")
        await channel.send(
            content=f"<@{ticket.user_id}>",
            embed=notice("Обращение будет автоматически закрыто через 24 часа без активности. "
                         "Ответьте в этом канале, если оно ещё актуально.", kind="warning"),
            allowed_mentions=discord.AllowedMentions(users=[discord.Object(id=ticket.user_id)]),
        )
        await self.bot.scheduler.schedule(job_key=f"tickets.close.{ticket.id}.{ticket.updated_at}",
            handler="tickets.inactive_close", due_at=ticket.updated_at + days * 86400,
            payload={"ticket_id": ticket.id, "activity_at": ticket.updated_at, "days": days})

    async def _close_inactive(self, payload: dict) -> None:
        ticket_id = int(payload["ticket_id"])
        ticket = await tickets.get(self.bot.database, self.bot.config.guild_id, ticket_id)
        days = int(await self.bot.database.get_setting(self.bot.config.guild_id, "tickets_inactivity_days", 0))
        if ticket is None or ticket.status != "open" or days == 0:
            return
        if ticket.updated_at != int(payload["activity_at"]) or days != int(payload["days"]):
            await self._schedule_inactivity(ticket.id)
            return
        if int(time.time()) < ticket.updated_at + days * 86400:
            return
        if not await tickets.start_close(self.bot.database, ticket.guild_id, ticket.id, 0, "Закрыто автоматически"):
            return
        result = await self._delete_ticket(ticket)
        if not result.startswith("Обращение закрыто"):
            raise RuntimeError(f"Автозакрытие ticket_id={ticket.id}: ошибка удаления канала")

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        # Discord cache может оказаться неполным на старте, поэтому не переоткрываем приватные каналы автоматически.
        if self._loaded:
            return
        self._loaded = True
        await self._deliver_surveys()
        for ticket_id in await tickets.stale_creating(self.bot.database, self.bot.config.guild_id, int(time.time()) - 600):
            await tickets.fail_create(self.bot.database, self.bot.config.guild_id, ticket_id, "Создание прервано при перезапуске")
        for ticket in await tickets.list_tickets(self.bot.database, self.bot.config.guild_id, limit=1000):
            if ticket.status == "closing":
                try:
                    result = await self._delete_ticket(ticket)
                    if not result.startswith("Обращение закрыто"):
                        log.error("Не удалось завершить удаление тикета ticket_id=%s", ticket.id)
                except Exception:
                    log.exception("Восстановление удаления тикета ticket_id=%s", ticket.id)
                continue
            if ticket.status == "open" and ticket.channel_id:
                channel = await self.get_channel(ticket)
                if channel is None:
                    log.warning("Не удалось проверить канал #%s: возможно, у бота нет доступа", ticket.id)
                    continue
                try:
                    await channel.set_permissions(channel.guild.default_role, view_channel=False,
                        reason=f"[NSB-TICKET-{ticket.id}] Проверка приватности после запуска")
                    await self._schedule_inactivity(ticket.id)
                    await self._schedule_first_reply_alert(ticket.id)
                    await self._schedule_sla_idle(ticket.id)
                except discord.HTTPException:
                    log.exception("Не удалось проверить приватность тикета #%s", ticket.id)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if not message.guild or message.guild.id != self.bot.config.guild_id or message.author.bot:
            return
        now = int(time.time())
        ticket = await tickets.by_channel(self.bot.database, message.guild.id, message.channel.id)
        if not ticket or ticket.status != "open":
            return
        member = message.author
        staff = isinstance(member, discord.Member) and (
            member.guild_permissions.administrator or any(r.id == ticket.staff_role_id for r in member.roles))
        if staff and ticket.first_response_at is None or now - self._last_message.get(message.channel.id, 0) >= 60:
            self._last_message[message.channel.id] = now
            await tickets.touch(self.bot.database, ticket.guild_id, ticket.id, member.id, staff_reply=staff)
            await self._schedule_inactivity(ticket.id)
            await self._schedule_sla_idle(ticket.id)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        if channel.guild.id != self.bot.config.guild_id:
            return
        ticket = await tickets.by_channel(self.bot.database, channel.guild.id, channel.id)
        if ticket and ticket.status == "closing":
            return  # Удаление инициировано ботом; вызывающий код очистит БД.
        if ticket:
            await tickets.erase_ticket(self.bot.database, ticket.guild_id, ticket.id, closing_only=False)

    @tickets.command(name="panel", description="Опубликовать панель обращения в поддержку")
    async def panel(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None) -> None:
        if not await self.require_admin(interaction):
            return
        if channel is None:
            channel_id = await self.bot.database.get_setting(self.bot.config.guild_id, "ticket_panel_channel_id")
            channel = interaction.guild.get_channel(channel_id) if interaction.guild and channel_id else None
        if not isinstance(channel, discord.TextChannel) or channel.guild.id != self.bot.config.guild_id:
            await interaction.response.send_message("Сначала выберите текстовый канал для панели в /botsettings.", ephemeral=True)
            return
        embed = card("NewSquad  /  Поддержка",
            "**Нужна помощь?** Откройте приватное обращение кнопкой ниже.\n\n"
            "Выберите тему и кратко опишите ситуацию. Доступ получат только вы и сотрудники поддержки.\n"
            "После закрытия канал удаляется вместе с данными обращения.")
        try:
            msg = await channel.send(embed=embed, view=TicketOpenView(self),
                                     allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            await interaction.response.send_message("Не удалось опубликовать панель. Проверьте права бота.", ephemeral=True)
            return
        await tickets.save_panel(self.bot.database, self.bot.config.guild_id, channel.id, msg.id, interaction.user.id)
        await interaction.response.send_message(f"Панель опубликована в {channel.mention}.", ephemeral=True)

    @tickets.command(name="settings", description="Показать настройки тикетов")
    async def settings(self, interaction: discord.Interaction) -> None:
        if not await self.require_admin(interaction):
            return
        guild_id = self.bot.config.guild_id
        cat = await self.bot.database.get_setting(guild_id, "ticket_category_channel_id")
        role = await self.bot.database.get_setting(guild_id, "support_role_id")
        enabled = await self.bot.database.get_setting(guild_id, "tickets_enabled", True)
        max_open = await self.bot.database.get_setting(guild_id, "tickets_max_open", 3)
        inactivity_days = await self.bot.database.get_setting(guild_id, "tickets_inactivity_days", 0)
        cats = await tickets.categories(self.bot.database, guild_id, enabled_only=False)
        description = (f"Система: **{'включена' if enabled else 'отключена'}**\n"
            f"Категория каналов: {f'<#{cat}>' if cat else 'не задана'}\n"
            f"Роль поддержки: {f'<@&{role}>' if role else 'не задана'}\n"
            "Журнал тикетов: отключён\n"
            f"Максимум открытых: **{max_open}**\n"
            "Закрытие: **удаление канала и данных без сохранения**\n"
            f"Автозакрытие после: **{inactivity_days}** дней (0 — выключено)\n\n" +
            "\n".join(f"#{c.id} · **{c.title}** — {'активна' if c.enabled else 'отключена'}"
                f"{' · доступна кандидатам' if c.candidate_allowed else ''}" for c in cats))
        await interaction.response.send_message(embed=discord.Embed(title="Настройки тикетов", description=description,
            color=BRAND_BLUE), ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @tickets.command(name="limit", description="Установить лимит открытых тикетов на человека")
    async def limit(self, interaction: discord.Interaction, amount: app_commands.Range[int, 1, 10]) -> None:
        if not await self.require_admin(interaction):
            return
        await self.bot.database.set_setting(self.bot.config.guild_id, "tickets_max_open", amount,
                                             actor_id=interaction.user.id)
        await interaction.response.send_message(f"Лимит: {amount}.", ephemeral=True)

    @tickets.command(name="inactivity-days", description="Закрывать тикеты после N дней без сообщений; 0 отключает")
    async def inactivity(self, interaction: discord.Interaction, days: app_commands.Range[int, 0, 30]) -> None:
        if not await self.require_admin(interaction):
            return
        await self.bot.database.set_setting(self.bot.config.guild_id, "tickets_inactivity_days", days,
                                             actor_id=interaction.user.id)
        if days:
            for ticket in await tickets.list_tickets(self.bot.database, self.bot.config.guild_id):
                if ticket.status == "open":
                    await self._schedule_inactivity(ticket.id)
        await interaction.response.send_message(
            f"Автозакрытие после {days} дней без активности, с предупреждением за 24 часа."
            if days else "Автоматическое закрытие отключено.", ephemeral=True)

    @tickets.command(name="enabled", description="Включить или отключить новые обращения")
    async def enabled(self, interaction: discord.Interaction, value: bool) -> None:
        if not await self.require_admin(interaction):
            return
        await self.bot.database.set_setting(self.bot.config.guild_id, "tickets_enabled", value,
                                             actor_id=interaction.user.id)
        await interaction.response.send_message("Приём обращений включён." if value else "Приём отключён.", ephemeral=True)

    @tickets.command(name="list", description="Открытые обращения (или свои обращения)")
    async def list(self, interaction: discord.Interaction) -> None:
        staff = await self.staff(interaction)
        rows = await tickets.list_tickets(self.bot.database, self.bot.config.guild_id,
            user_id=None if staff else interaction.user.id)
        lines = [f"**#{t.id}** · {discord.utils.escape_mentions(t.subject)} · {t.status} · "
                 + (f"<#{t.channel_id}>" if t.channel_id else "канал создаётся") for t in rows]
        await interaction.response.send_message("\n".join(lines)[:1900] or "Открытых обращений нет.", ephemeral=True,
                                                allowed_mentions=discord.AllowedMentions.none())


    @tickets.command(name="mine", description="Мои обращения и тикеты, назначенные мне")
    async def mine(self, interaction: discord.Interaction) -> None:
        rows = await tickets.list_tickets(self.bot.database, interaction.guild_id, limit=100)
        staff = await self.staff(interaction)
        mine = [t for t in rows if t.user_id == interaction.user.id or (
            staff and t.assignee_id == interaction.user.id)]
        lines = [f"**#{t.id:05d}** · {t.status} · "
                 f"{discord.utils.escape_mentions(t.subject[:65])} · "
                 f"{f'<#{t.channel_id}>' if t.channel_id else 'создаётся'}"
                 for t in mine[:25]]
        await interaction.response.send_message(embed=panel_card(
            "NewSquad / Мои обращения", "\n".join(lines)[:3650] if lines
            else "Открытых обращений нет."), ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none())

    @tickets.command(name="unanswered", description="Обращения без ответа сотрудника")
    async def unanswered(self, interaction: discord.Interaction,
                         older_hours: app_commands.Range[int, 1, 168] = 6) -> None:
        if not await self.staff(interaction):
            await interaction.response.send_message("Нужна роль поддержки.", ephemeral=True)
            return
        rows = await tickets.list_tickets(self.bot.database, interaction.guild_id, limit=500)
        cutoff = int(time.time()) - older_hours * 3600
        entries = [t for t in rows if t.status == "open" and t.opened_at <= cutoff
                   and t.first_response_at is None]
        lines = [f"**#{t.id:05d}** · <#{t.channel_id}> · <t:{t.opened_at}:R> · "
                 f"{'<@'+str(t.assignee_id)+'>' if t.assignee_id else 'не назначен'}"
                 for t in entries[:20] if await self.staff(interaction, t)]
        await interaction.response.send_message(embed=panel_card(
            "NewSquad / Ожидают ответа", "\n".join(lines) if lines
            else "Просроченных обращений нет."), ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none())

    @tickets.command(name="reply-alert", description="Порог первого ответа в часах; 0 отключает")
    async def reply_alert(self, interaction: discord.Interaction,
                          hours: app_commands.Range[int, 0, 168]) -> None:
        if not await self.require_admin(interaction):
            return
        await self.bot.database.set_setting(interaction.guild_id,
            "tickets_reply_alert_hours", hours, actor_id=interaction.user.id)
        await interaction.response.send_message(
            "Порог уведомления сохранён. Применяется к новым тикетам.", ephemeral=True)

    @tickets.command(name="claim", description="Принять обращение в работу")
    async def claim_command(self, interaction: discord.Interaction, ticket_id: int) -> None:
        ticket = await tickets.get(self.bot.database, self.bot.config.guild_id, ticket_id)
        if not ticket or not await self.staff(interaction, ticket):
            await interaction.response.send_message("Нет доступа к этому обращению.", ephemeral=True)
            return
        ok = await tickets.claim(self.bot.database, ticket.guild_id, ticket_id, interaction.user.id)
        if ok:
            await self.refresh(ticket_id)
            await self.audit("Тикет принят в работу", ticket_id, interaction.user.id)
        await interaction.response.send_message("Вы назначены ответственным." if ok else "Тикет уже назначен или закрыт.", ephemeral=True)

    @tickets.command(name="transfer", description="Передать обращение другому сотруднику")
    async def transfer(self, interaction: discord.Interaction, ticket_id: int, member: discord.Member) -> None:
        ticket = await tickets.get(self.bot.database, self.bot.config.guild_id, ticket_id)
        if not ticket or not await self.staff(interaction, ticket):
            await interaction.response.send_message("Нет прав.", ephemeral=True)
            return
        if not self._staff_member(member, ticket):
            await interaction.response.send_message("Получатель должен быть сотрудником поддержки.", ephemeral=True)
            return
        ok = await tickets.claim(self.bot.database, ticket.guild_id, ticket_id, member.id, transfer=True)
        if ok:
            await self.refresh(ticket_id)
            await self.audit("Тикет передан", ticket_id, interaction.user.id, f"Ответственный: {member.id}")
        await interaction.response.send_message("Ответственный изменён." if ok else "Тикет закрыт.", ephemeral=True)

    def _staff_member(self, member: discord.Member, ticket: Ticket) -> bool:
        return member.guild_permissions.administrator or any(r.id == ticket.staff_role_id for r in member.roles)

    @tickets.command(name="close", description="Закрыть обращение и безвозвратно удалить канал")
    async def close_command(self, interaction: discord.Interaction, ticket_id: int) -> None:
        ticket = await tickets.get(self.bot.database, self.bot.config.guild_id, ticket_id)
        if not ticket or (ticket.user_id != interaction.user.id and not await self.staff(interaction, ticket)):
            await interaction.response.send_message(embed=notice("Обращение недоступно.", kind="warning"),
                                                    ephemeral=True)
            return
        await interaction.response.send_message(
            embed=card("Подтверждение удаления", "После закрытия канал и история тикета исчезнут безвозвратно."),
            view=ConfirmTicketDeletion(self, ticket_id, interaction.user.id), ephemeral=True)

    @tickets.command(name="add", description="Добавить участника к обращению")
    async def add(self, interaction: discord.Interaction, ticket_id: int, member: discord.Member) -> None:
        await self.edit_participant(interaction, ticket_id, member, remove=False)

    @tickets.command(name="remove", description="Убрать участника из обращения")
    async def remove(self, interaction: discord.Interaction, ticket_id: int, member: discord.Member) -> None:
        await self.edit_participant(interaction, ticket_id, member, remove=True)

    async def edit_participant(self, interaction: discord.Interaction, ticket_id: int, member: discord.Member,
                               *, remove: bool) -> None:
        ticket = await tickets.get(self.bot.database, self.bot.config.guild_id, ticket_id)
        if not ticket or not await self.staff(interaction, ticket):
            await interaction.response.send_message("Нет прав.", ephemeral=True)
            return
        role_id = await self.bot.database.get_setting(ticket.guild_id, "candidate_role_id")
        if not remove and not await approved_member(self.bot.database, member, role_id):
            await interaction.response.send_message("Добавлять можно только одобренных участников NewSquad.", ephemeral=True)
            return
        channel = await self.get_channel(ticket)
        if channel is None or ticket.status != "open":
            await interaction.response.send_message("Приватный канал недоступен или тикет закрыт.", ephemeral=True)
            return
        if member.id == ticket.user_id:
            await interaction.response.send_message("Автора обращения нельзя изменить.", ephemeral=True)
            return
        before = channel.overwrites_for(member)
        try:
            updated = await tickets.participant(self.bot.database, ticket.guild_id, ticket.id,
                member.id, interaction.user.id, remove=remove)
            if updated:
                try:
                    await channel.set_permissions(member, overwrite=None if remove else discord.PermissionOverwrite(
                        view_channel=True, send_messages=True, read_message_history=True),
                        reason=f"[NSB-TICKET-{ticket.id}] Изменение участников")
                except discord.HTTPException:
                    await tickets.participant(self.bot.database, ticket.guild_id, ticket.id,
                        member.id, interaction.user.id, remove=not remove)
                    raise
        except (discord.HTTPException, ValueError):
            try:
                await channel.set_permissions(member, overwrite=before,
                    reason=f"[NSB-TICKET-{ticket.id}] Откат операции")
            except discord.HTTPException:
                log.exception("Не удалось восстановить права в тикете %s", ticket.id)
            await interaction.response.send_message("Не удалось изменить доступ к каналу.", ephemeral=True)
            return
        if updated:
            await self.audit("Изменён состав тикета", ticket.id, interaction.user.id,
                             f"Пользователь: {member.id}; удалить: {remove}")
        await interaction.response.send_message("Доступ обновлён." if updated else "Изменений нет.", ephemeral=True)

    @tickets.command(name="location", description="Выбрать категорию Discord для приватных каналов")
    async def location(self, interaction: discord.Interaction, category: discord.CategoryChannel) -> None:
        if not await self.require_admin(interaction):
            return
        if category.guild.id != self.bot.config.guild_id:
            await interaction.response.send_message("Недопустимая категория Discord.", ephemeral=True)
            return
        await self.bot.database.set_setting(self.bot.config.guild_id, "ticket_category_channel_id", category.id,
                                             actor_id=interaction.user.id)
        await interaction.response.send_message(f"Приватные обращения будут создаваться в **{category.name}**.", ephemeral=True)

    @categories_group.command(name="create", description="Добавить категорию обращений")
    async def category_create(self, interaction: discord.Interaction, key: str, title: str, description: str,
                              support_role: discord.Role | None = None, allow_candidates: bool = False) -> None:
        if not await self.require_admin(interaction):
            return
        if support_role and (support_role.guild.id != self.bot.config.guild_id or support_role.is_default()):
            await interaction.response.send_message("Недопустимая роль поддержки.", ephemeral=True)
            return
        if len(await tickets.categories(self.bot.database, self.bot.config.guild_id, enabled_only=False)) >= 25:
            await interaction.response.send_message("Достигнут лимит 25 категорий.", ephemeral=True)
            return
        try:
            category = await tickets.add_category(self.bot.database, self.bot.config.guild_id, key,
                title, description, support_role_id=support_role.id if support_role else None,
                candidate_allowed=allow_candidates)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.send_message(f"Категория **{category.title}** создана (#{category.id}).", ephemeral=True)

    @categories_group.command(name="edit", description="Изменить категорию обращений")
    async def category_edit(self, interaction: discord.Interaction, category_id: int,
                            title: str | None = None, description: str | None = None,
                            enabled: bool | None = None, allow_candidates: bool | None = None,
                            support_role: discord.Role | None = None) -> None:
        if not await self.require_admin(interaction):
            return
        try:
            ok = await tickets.update_category(self.bot.database, self.bot.config.guild_id, category_id,
                title=title, description=description, enabled=enabled, candidate_allowed=allow_candidates,
                support_role_id=support_role.id if support_role else None, role_supplied=support_role is not None)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.send_message("Категория изменена." if ok else "Изменений нет.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(Tickets(bot), guild=discord.Object(id=bot.config.guild_id))
