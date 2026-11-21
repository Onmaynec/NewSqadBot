"""Вступление в NewSquad: типизированные формы, приватное рассмотрение и решения."""
from __future__ import annotations

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from app.config import FEMALE_ROLE_ID
from app.core.durations import NICKNAME_TEMPLATES, membership_nickname
from app.core.permissions import can_manage_settings
from app.modules.membership_views import (
    ApplicationForm as ApplicationForm,
    ApplyPanel as ApplyPanel,
    FollowupQuestion as FollowupQuestion,
    FormTypeSelect as FormTypeSelect,
    NewMemberGuide as NewMemberGuide,
    RejectForm as RejectForm,
    ReviewView as ReviewView,
    application_embed as application_embed,
    clean as clean,
)
from app.services import applications as store
from app.services.audit import log_event
from app.services.membership_access import approved_member
from app.services.membership_workflow import review_due_at
from app.ui.mentions import channel_mention, role_mention
from app.ui.theme import BRAND_BLUE, panel_card

log = logging.getLogger(__name__)


class Membership(commands.Cog):
    membership_commands = app_commands.Group(name="membership", description="Управление заявками NewSquad")
    application_commands = app_commands.Group(name="application", description="Ваша заявка в NewSquad")

    def __init__(self, bot):
        self.bot = bot
        self._execution_locks: dict[int, asyncio.Lock] = {}
        self._recovered = False
        if getattr(bot, "scheduler", None) is not None:
            bot.scheduler.register("membership.review_reminder", self._send_review_reminder)

    async def cog_load(self) -> None:
        self.bot.add_view(ApplyPanel(self))
        for app in await store.active_reviews(self.bot.database, self.bot.config.guild_id):
            self.bot.add_view(ReviewView(self, app.id), message_id=app.review_message_id)

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._recovered:
            return
        self._recovered = True
        # Восстанавливаем и карточки, и решения, прерванные рестартом Docker.
        for app in await store.awaiting_publication(self.bot.database, self.bot.config.guild_id):
            try:
                await self.publish(app)
            except (discord.HTTPException, RuntimeError):
                log.exception("Не удалось восстановить карточку заявки %s", app.id)
        for app in await store.active_reviews(self.bot.database, self.bot.config.guild_id):
            await self.schedule_review_reminder(app)
        for app in await store.unfinished(self.bot.database, self.bot.config.guild_id):
            if app.decision not in ("approve", "reject"):
                continue
            try:
                result = await self.execute(app.id)
                log.info("Восстановлена заявка #%s: %s", app.id, result)
                await self.refresh(app.id)
            except Exception:
                log.exception("Не удалось восстановить решение заявки #%s", app.id)

    async def schedule_review_reminder(self, application) -> None:
        if application.status not in store.REVIEWABLE or getattr(self.bot, "scheduler", None) is None:
            return
        hours = int(await self.bot.database.get_setting(
            self.bot.config.guild_id, "membership_review_wait_hours", 12))
        if not 1 <= hours <= 168:
            return
        start = application.stage_changed_at or application.created_at
        await self.bot.scheduler.schedule(
            job_key=f"membership:review:{application.id}:{start}",
            handler="membership.review_reminder",
            due_at=review_due_at(application, waiting_hours=hours),
            payload={"application_id": application.id, "stage_at": start},
        )

    async def _send_review_reminder(self, payload: dict) -> None:
        app = await store.get_application(self.bot.database, int(payload["application_id"]))
        if app is None or app.guild_id != self.bot.config.guild_id or app.status not in store.REVIEWABLE:
            return
        if (app.stage_changed_at or app.created_at) != int(payload["stage_at"]):
            await self.schedule_review_reminder(app)
            return
        if not app.reviewer_id:
            log.info("Заявка #%s ожидает назначения сотрудника; уведомлять пока некого", app.id)
            return
        recipient = self.bot.get_user(app.reviewer_id) or await self.bot.fetch_user(app.reviewer_id)
        await recipient.send(
            f"NewSqad: заявка #{app.id:04d} долго ожидает решения ({app.status}). "
            "Откройте канал рассмотрения. Личные данные кандидата не прикрепляются.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def can_review(self, interaction: discord.Interaction) -> bool:
        if interaction.guild_id != self.bot.config.guild_id:
            return False
        if can_manage_settings(interaction, self.bot.config.owner_ids):
            return True
        reviewer_role_id = await self.bot.database.get_setting(interaction.guild_id, "reviewer_role_id")
        return bool(reviewer_role_id and isinstance(interaction.user, discord.Member)
                    and any(role.id == reviewer_role_id for role in interaction.user.roles))

    async def publish(self, app) -> None:
        if app.review_message_id:
            return
        channel_id = await self.bot.database.get_setting(self.bot.config.guild_id, "review_channel_id")
        guild = self.bot.get_guild(self.bot.config.guild_id)
        channel = guild.get_channel(channel_id) if guild and channel_id else None
        if not isinstance(channel, discord.TextChannel):
            raise RuntimeError("Сначала настройте канал рассмотрения через /botsettings")
        view = ReviewView(self, app.id)
        message = await channel.send(embed=application_embed(app), view=view,
                                     allowed_mentions=discord.AllowedMentions.none())
        await store.set_review_message(self.bot.database, app.id, channel.id, message.id)
        self.bot.add_view(view, message_id=message.id)

    async def refresh(self, app_id: int) -> None:
        app = await store.get_application(self.bot.database, app_id)
        if not app or not app.review_message_id:
            return
        guild = self.bot.get_guild(self.bot.config.guild_id)
        channel = guild.get_channel(app.review_channel_id) if guild else None
        if not isinstance(channel, discord.TextChannel):
            return
        try:
            message = await channel.fetch_message(app.review_message_id)
            active = app.status in store.OPEN
            await message.edit(embed=application_embed(app),
                               view=ReviewView(self, app.id) if active else None,
                               allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            log.exception("Не удалось обновить заявку #%s", app_id)

    async def decide(self, interaction: discord.Interaction, app_id: int, *, approve: bool, reason: str) -> str:
        if not await self.can_review(interaction):
            return "Недостаточно прав."
        app = await store.get_application(self.bot.database, app_id)
        if not app or app.guild_id != self.bot.config.guild_id:
            return "Заявка не найдена."
        target = "approving" if approve else "rejecting"
        locked = await store.transition(self.bot.database, app_id, expected=store.REVIEWABLE,
                                        target=target, actor_id=interaction.user.id, reason=reason,
                                        decision="approve" if approve else "reject")
        if not locked:
            return "Решение уже принято или заявка обрабатывается. Для ошибок используйте /membership retry."
        result = await self.execute(app_id)
        await self.refresh(app_id)
        return result

    async def execute(self, app_id: int) -> str:
        lock = self._execution_locks.setdefault(app_id, asyncio.Lock())
        async with lock:
            return await self._execute_locked(app_id)

    async def _execute_locked(self, app_id: int) -> str:
        app = await store.get_application(self.bot.database, app_id)
        if not app or app.status not in ("approving", "rejecting", "action_failed"):
            return "Нет незавершённых действий."
        guild = self.bot.get_guild(self.bot.config.guild_id)
        if guild is None:
            return "Сервер NewSquad не найден."
        try:
            member = guild.get_member(app.user_id) or await guild.fetch_member(app.user_id)
        except discord.NotFound:
            if app.decision == "reject":
                await store.step(self.bot.database, app_id, "kick_status", "done")
                await store.transition(self.bot.database, app_id, expected=(app.status,),
                    target="rejected", actor_id=app.reviewer_id or 0, reason="Участник уже покинул сервер")
                return "Кандидат уже покинул сервер; отказ сохранён."
            await store.fail(self.bot.database, app_id, app.reviewer_id or 0, "Участник вышел до одобрения")
            return "Кандидат покинул сервер, доступ не выдан."
        except discord.HTTPException as exc:
            return f"Не удалось получить участника: {exc}"
        if app.decision == "approve":
            role_id = await self.bot.database.get_setting(app.guild_id, "member_role_id")
            role = guild.get_role(role_id) if role_id else None
            if role is None:
                await store.fail(self.bot.database, app_id, app.reviewer_id or 0, "Не настроена роль участника")
                return "Не настроена роль участника. Укажите её в /botsettings."
            if getattr(member, "pending", False):
                await store.fail(self.bot.database, app_id, app.reviewer_id or 0, "Не пройдена встроенная проверка Discord")
                return "Кандидат ещё не прошёл встроенную проверку правил Discord."
            # Проверить все роли до изменения доступа. При частичном сбое
            # откатываем роль Member, чтобы не раскрыть приватные каналы.
            candidate_id = await self.bot.database.get_setting(app.guild_id, "candidate_role_id")
            female_id = await self.bot.database.get_setting(app.guild_id, "female_role_id", FEMALE_ROLE_ID)
            candidate_role = guild.get_role(candidate_id) if candidate_id else None
            female = guild.get_role(female_id) if app.gender == "female" else None
            me = guild.me
            try:
                if candidate_id == role.id or (candidate_id and candidate_role is None):
                    raise RuntimeError("Роль кандидата отсутствует или совпадает с ролью участника")
                if app.gender == "female" and female is None:
                    raise RuntimeError("Не найдена женская роль")
                if (me is None or not me.guild_permissions.manage_roles or me.top_role <= role
                        or me.top_role <= member.top_role):
                    raise RuntimeError("У бота недостаточно прав для выдачи роли участника")
                for managed in (female, candidate_role):
                    if managed is not None and me.top_role <= managed:
                        raise RuntimeError("Роль бота должна быть выше управляемых ролей")
            except RuntimeError as exc:
                await store.fail(self.bot.database, app_id, app.reviewer_id or 0, str(exc))
                return f"Проверка ролей не пройдена: {exc}. Исправьте настройки и повторите."

            added_female = False
            member_role_touched = False
            try:
                if role not in member.roles:
                    await member.add_roles(role, reason=f"[NSB-APPLICATION-{app_id:04d}] Одобрение")
                member_role_touched = True
                await store.step(self.bot.database, app_id, "role_status", "done")
                if female is not None:
                    if female not in member.roles:
                        await member.add_roles(female, reason=f"[NSB-APPLICATION-{app_id:04d}] Женская роль")
                        added_female = True
                    await store.step(self.bot.database, app_id, "female_role_status", "done")
                else:
                    await store.step(self.bot.database, app_id, "female_role_status", "n_a")
                if candidate_role is not None and candidate_role in member.roles:
                    await member.remove_roles(candidate_role,
                        reason=f"[NSB-APPLICATION-{app_id:04d}] Кандидат принят")
                await store.step(self.bot.database, app_id, "candidate_role_status",
                                 "done" if candidate_role is not None else "n_a")
            except (discord.HTTPException, RuntimeError) as exc:
                log.exception("Не завершена выдача доступа application_id=%s", app_id)
                rollback_errors = []
                try:
                    if member_role_touched:
                        await member.remove_roles(role, reason=f"[NSB-APPLICATION-{app_id:04d}] Откат")
                    await store.step(self.bot.database, app_id, "role_status", "pending")
                except discord.HTTPException as rollback_exc:
                    rollback_errors.append(f"основная роль: {rollback_exc}")
                if added_female and female is not None:
                    try:
                        await member.remove_roles(female, reason=f"[NSB-APPLICATION-{app_id:04d}] Откат")
                        await store.step(self.bot.database, app_id, "female_role_status", "pending")
                    except discord.HTTPException as rollback_exc:
                        rollback_errors.append(f"женская роль: {rollback_exc}")
                detail = f"Ошибка выдачи доступа: {exc}"
                if rollback_errors:
                    detail += "; откат не выполнен: " + ", ".join(rollback_errors)
                    log.critical("Требуется ручное снятие ролей application_id=%s: %s", app_id, detail)
                await store.fail(self.bot.database, app_id, app.reviewer_id or 0, detail)
                return f"Доступ не подтверждён: {detail}. Используйте /membership retry."
            warning = ""
            try:
                nick_template = await self.bot.database.get_setting(app.guild_id, "membership_nickname_template", "default")
                new_nick = membership_nickname(member.name, app.name, nick_template)
                if member.nick != new_nick:
                    await member.edit(nick=new_nick, reason=f"[NSB-APPLICATION-{app_id:04d}] Одобрение")
                await store.step(self.bot.database, app_id, "nickname_status", "done")
            except (discord.HTTPException, ValueError) as exc:
                warning = f" Ник не изменён: {exc}."
                await store.step(self.bot.database, app_id, "nickname_status", "failed", str(exc))
            await store.transition(self.bot.database, app_id, expected=(app.status,),
                target="approved", actor_id=app.reviewer_id or 0, reason="Доступ выдан" + warning)
            app = await store.get_application(self.bot.database, app_id)
            if app.dm_status != "done":
                try:
                    await member.send(embed=discord.Embed(
                        title="✅ NewSquad · Заявка одобрена",
                        description=(f"Ваша заявка **#{app_id:04d}** принята.\n"
                                     "Вам открыт доступ к сообществу. Добро пожаловать!"),
                        color=BRAND_BLUE))
                    await store.step(self.bot.database, app_id, "dm_status", "done")
                except discord.HTTPException:
                    await store.step(self.bot.database, app_id, "dm_status", "failed")
            # Durable-событие сохранено атомарно с APPROVED в store.transition.
            try:
                await log_event(self.bot, channel_setting="membership_audit_channel_id",
                    title=f"Заявка #{app_id:04d} одобрена",
                    details=f"Кандидат: {app.user_id}\\nМодератор: {app.reviewer_id}\\n{warning}",
                    actor_id=app.reviewer_id or 0, action="membership.approved", target_id=app.user_id)
            except Exception:
                log.exception("Не удалось записать аудит одобрения application_id=%s", app_id)
            return "Кандидат принят." + warning
        # REJECT: DM before kick; unavailable DMs must not block kick.
        if app.dm_status == "pending":
            try:
                await member.send(embed=discord.Embed(
                    title="NewSquad · Решение по заявке",
                    description=(f"Заявка **#{app_id:04d}** отклонена.\n"
                                 f"**Причина:** {discord.utils.escape_mentions(app.decision_reason or 'Не указана')}\n\n"
                                 "Доступ к серверу будет закрыт."),
                    color=BRAND_BLUE), allowed_mentions=discord.AllowedMentions.none())
                await store.step(self.bot.database, app_id, "dm_status", "done")
            except discord.HTTPException:
                await store.step(self.bot.database, app_id, "dm_status", "failed")
        try:
            await guild.kick(member, reason=f"[NSB-APPLICATION-{app_id:04d}] {app.decision_reason}"[:512])
            await store.step(self.bot.database, app_id, "kick_status", "done")
        except discord.HTTPException as exc:
            await store.fail(self.bot.database, app_id, app.reviewer_id or 0, f"Не удалось исключить: {exc}")
            return f"Решение сохранено, но исключить кандидата не удалось: {exc}"
        await store.transition(self.bot.database, app_id, expected=(app.status,),
            target="rejected", actor_id=app.reviewer_id or 0, reason="Кандидат исключён")
        await log_event(self.bot, channel_setting="membership_audit_channel_id",
            title=f"Заявка #{app_id:04d} отклонена", details=f"Кандидат: {app.user_id}\nПричина: {app.decision_reason}",
            actor_id=app.reviewer_id or 0, action="membership.rejected", target_id=app.user_id)
        return "Решение зафиксировано, кандидат исключён."


    async def request_info(self, interaction: discord.Interaction, number: int, question: str) -> str:
        if not await self.can_review(interaction):
            return "Нет прав."
        question = question.strip()
        if not 5 <= len(question) <= 500:
            return "Вопрос должен содержать 5–500 символов."
        app = await store.get_application(self.bot.database, number)
        if not app or app.guild_id != interaction.guild_id:
            return "Заявка не найдена."
        done = await store.transition(self.bot.database, number, expected=("pending", "in_review"),
            target="needs_info", actor_id=interaction.user.id, reason="Уточнение: " + question)
        if not done:
            return "Заявка уже закрыта или ожидает ответа."
        await self.refresh(number)
        try:
            user = self.bot.get_user(app.user_id) or await self.bot.fetch_user(app.user_id)
            await user.send(embed=discord.Embed(title="NewSquad / Уточнение заявки",
                description=f"Заявка #{number:04d}\n{discord.utils.escape_mentions(question)}\n"
                            "Ответьте командой /application answer на сервере.",
                color=BRAND_BLUE), allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            return "Вопрос сохранён, но личное сообщение отправить не удалось."
        return "Вопрос отправлен кандидату."

    @membership_commands.command(name="request-info", description="Запросить уточнение у кандидата")
    async def request_info_cmd(self, interaction: discord.Interaction, number: int, question: str) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        await interaction.followup.send(await self.request_info(interaction, number, question), ephemeral=True)

    @membership_commands.command(name="transfer", description="Передать рассмотрение заявки")
    async def transfer(self, interaction: discord.Interaction, number: int, reviewer: discord.Member) -> None:
        if not await self.can_review(interaction):
            await interaction.response.send_message("Нет доступа.", ephemeral=True)
            return
        role_id = await self.bot.database.get_setting(interaction.guild_id, "reviewer_role_id")
        if reviewer.bot or not (reviewer.guild_permissions.administrator or (
                role_id and any(r.id == role_id for r in reviewer.roles))):
            await interaction.response.send_message("У сотрудника нет доступа к рассмотрению.", ephemeral=True)
            return
        updated = await store.assign_reviewer(
            self.bot.database, interaction.guild_id, number, interaction.user.id, reviewer.id)
        if updated:
            await self.refresh(number)
            await self.schedule_review_reminder(await store.get_application(self.bot.database, number))
        await interaction.response.send_message(
            "Ответственный изменён." if updated else "Заявка недоступна для передачи.", ephemeral=True)

    @membership_commands.command(name="timeline", description="Приватная история действий с заявкой")
    async def timeline_cmd(self, interaction: discord.Interaction, number: int) -> None:
        if not await self.can_review(interaction):
            await interaction.response.send_message("Нет доступа.", ephemeral=True)
            return
        entries = await store.timeline(self.bot.database, interaction.guild_id, number)
        lines = [f"<t:{e.created_at}:f> · <@{e.actor_id}> · {e.old_status} → {e.new_status}\n"
                 f"{discord.utils.escape_mentions(e.reason[:230])}" for e in entries]
        await interaction.response.send_message("\n\n".join(lines)[:1900] or "История пуста.",
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @membership_commands.command(name="nickname", description="Настроить шаблон ника для новых участников")
    async def nickname_cmd(self, interaction: discord.Interaction, template: str) -> None:
        if not can_manage_settings(interaction, self.bot.config.owner_ids):
            await interaction.response.send_message("Только администрация.", ephemeral=True)
            return
        if template not in NICKNAME_TEMPLATES:
            await interaction.response.send_message(
                "Шаблоны: default, name_first, name_only, username_only.", ephemeral=True)
            return
        await self.bot.database.set_setting(interaction.guild_id, "membership_nickname_template",
            template, actor_id=interaction.user.id)
        await interaction.response.send_message(
            f"Новый формат ника: {NICKNAME_TEMPLATES[template]}", ephemeral=True)

    @application_commands.command(name="answer", description="Ответить на запрос уточнения по заявке")
    async def answer(self, interaction: discord.Interaction, text: str) -> None:
        app = await store.get_active(self.bot.database, interaction.guild_id, interaction.user.id)
        if app is None or app.status != "needs_info":
            await interaction.response.send_message("Сейчас уточнение не запрошено.", ephemeral=True)
            return
        try:
            updated = await store.submit_answer(
                self.bot.database, interaction.guild_id, app.id, interaction.user.id, text)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        if updated:
            await self.refresh(app.id)
        await interaction.response.send_message(
            "Ответ сохранён." if updated else "Ответ уже обработан.", ephemeral=True)

    @membership_commands.command(name="panel", description="Опубликовать панель подачи заявок")
    async def panel(self, interaction: discord.Interaction) -> None:
        if not can_manage_settings(interaction, self.bot.config.owner_ids):
            await interaction.response.send_message("Требуются права администратора.", ephemeral=True)
            return
        channel_id = await self.bot.database.get_setting(interaction.guild_id, "application_channel_id")
        channel = interaction.guild.get_channel(channel_id) if channel_id else None
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("Сначала выберите канал заявок в /botsettings.", ephemeral=True)
            return
        await channel.send(embed=discord.Embed(title="NewSquad · Вступление",
            description="Заполните анкету: имя, пол, возраст и кто пригласил. После рассмотрения вы получите доступ к сообществу.",
            color=BRAND_BLUE), view=ApplyPanel(self))
        await interaction.response.send_message("Панель опубликована.", ephemeral=True)

    @membership_commands.command(name="applications", description="Список активных заявок")
    async def applications(self, interaction: discord.Interaction) -> None:
        if not await self.can_review(interaction):
            await interaction.response.send_message("Нет доступа.", ephemeral=True)
            return
        rows = await store.active_reviews(self.bot.database, self.bot.config.guild_id)
        rows += await store.awaiting_publication(self.bot.database, self.bot.config.guild_id)
        lines = [f"#{app.id:04d} — <@{app.user_id}> — {app.status}" for app in rows[:20]]
        await interaction.response.send_message("\n".join(lines) if lines else "Активных заявок нет.", ephemeral=True)

    @membership_commands.command(name="view", description="Показать заявку по номеру")
    async def view_application(self, interaction: discord.Interaction, number: int) -> None:
        if not await self.can_review(interaction):
            await interaction.response.send_message("Нет доступа.", ephemeral=True)
            return
        app = await store.get_application(self.bot.database, number)
        if not app or app.guild_id != interaction.guild_id:
            await interaction.response.send_message("Заявка не найдена.", ephemeral=True)
            return
        await interaction.response.send_message(embed=application_embed(app), ephemeral=True,
                                                allowed_mentions=discord.AllowedMentions.none())

    @membership_commands.command(name="approve", description="Одобрить заявку")
    async def approve_cmd(self, interaction: discord.Interaction, number: int) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await self.decide(interaction, number, approve=True, reason="Одобрено")
        await self.refresh(number)
        await interaction.followup.send(result, ephemeral=True)

    @membership_commands.command(name="reject", description="Отклонить заявку и исключить кандидата")
    async def reject_cmd(self, interaction: discord.Interaction, number: int, reason: str) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await self.decide(interaction, number, approve=False, reason=reason[:400])
        await self.refresh(number)
        await interaction.followup.send(result, ephemeral=True)

    @membership_commands.command(name="retry", description="Повторить незавершённые действия по заявке")
    async def retry(self, interaction: discord.Interaction, number: int) -> None:
        if not await self.can_review(interaction):
            await interaction.response.send_message("Нет доступа.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        app = await store.get_application(self.bot.database, number)
        if not app or app.guild_id != interaction.guild_id or app.status not in ("action_failed", "approving", "rejecting"):
            await interaction.followup.send("Нет незавершённых действий по этой заявке.", ephemeral=True)
            return
        result = await self.execute(number)
        await self.refresh(number)
        await interaction.followup.send(result, ephemeral=True)

    @membership_commands.command(name="history", description="История заявок участника")
    async def history_cmd(self, interaction: discord.Interaction, user: discord.User) -> None:
        if not await self.can_review(interaction):
            await interaction.response.send_message("Нет доступа.", ephemeral=True)
            return
        history = await store.past_applications(self.bot.database, interaction.guild_id, user.id)
        await interaction.response.send_message("\n".join(
            f"#{item.id:04d}: {item.status} — {item.decision_reason or 'Без комментария'}" for item in history)[:1800]
            or "Заявок нет.", ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @membership_commands.command(name="settings", description="Просмотреть настройки Membership")
    async def settings(self, interaction: discord.Interaction) -> None:
        if not can_manage_settings(interaction, self.bot.config.owner_ids):
            await interaction.response.send_message("Нет доступа.", ephemeral=True)
            return
        roles = (
            ("member_role_id", "Роль участника"),
            ("candidate_role_id", "Роль кандидата"),
            ("female_role_id", "Женская роль"),
            ("reviewer_role_id", "Роль рассмотрения"),
        )
        channels = (
            ("application_channel_id", "Подача заявок"),
            ("review_channel_id", "Рассмотрение заявок"),
            ("membership_audit_channel_id", "Журнал заявок"),
        )
        guild = interaction.guild
        entries = []
        for key, label in roles:
            value = await self.bot.database.get_setting(
                interaction.guild_id, key, FEMALE_ROLE_ID if key == "female_role_id" else None,
            )
            entries.append(f"**{label}:** {role_mention(None, role_id=value, guild=guild)}")
        places = []
        for key, label in channels:
            value = await self.bot.database.get_setting(interaction.guild_id, key)
            places.append(f"**{label}:** {channel_mention(guild, value)}")
        await interaction.response.send_message(
            embed=panel_card(
                "NewSquad / Membership / Настройки",
                "Роли и каналы приёма участников.",
                ("Роли", "\n".join(entries)),
                ("Каналы", "\n".join(places)),
            ), ephemeral=True,
        )

    @application_commands.command(name="guide", description="Панель адаптации нового участника")
    async def guide_command(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id != self.bot.config.guild_id or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Команда доступна на NewSqad.", ephemeral=True)
            return
        candidate_id = await self.bot.database.get_setting(interaction.guild_id, "candidate_role_id")
        if not await approved_member(self.bot.database, interaction.user, candidate_id):
            await interaction.response.send_message(
                "Панель адаптации появится после одобрения заявки.", ephemeral=True)
            return
        await interaction.response.send_message(
            "**Добро пожаловать в NewSqad!** Выберите раздел:",
            view=NewMemberGuide(self.bot, interaction.user.id), ephemeral=True)

    @application_commands.command(name="status", description="Статус вашей анкеты")
    async def own_status(self, interaction: discord.Interaction) -> None:
        rows = await store.past_applications(self.bot.database, interaction.guild_id, interaction.user.id, limit=1)
        await interaction.response.send_message(
            f"Ваша последняя заявка #{rows[0].id:04d}: {rows[0].status}." if rows else "Вы ещё не подавали заявку.",
            ephemeral=True)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        if member.guild.id != self.bot.config.guild_id:
            return
        current = await store.get_active(self.bot.database, member.guild.id, member.id)
        if current and current.status in ("pending", "in_review", "needs_info"):
            await store.transition(self.bot.database, current.id, expected=(current.status,),
                target="withdrawn", actor_id=member.id, reason="Кандидат покинул сервер")
            await self.refresh(current.id)


async def setup(bot) -> None:
    await bot.add_cog(Membership(bot), guild=discord.Object(id=bot.config.guild_id))
