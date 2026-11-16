"""Вступление в NewSquad: одна анкета, приватное рассмотрение и восстановимые решения."""
from __future__ import annotations

import logging
import re

import discord
from discord import app_commands
from discord.ext import commands

from app.config import FEMALE_ROLE_ID
from app.core.durations import nickname
from app.core.permissions import can_manage_settings
from app.services import applications as store
from app.services.audit import log_event
from app.ui.theme import BRAND_GREEN, LoggedModal, LoggedView

log = logging.getLogger(__name__)


def clean(value: str, *, limit: int, allow_at: bool = False) -> str:
    value = value.strip()
    if not value or len(value) > limit or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f"Поле должно содержать от 1 до {limit} символов без переносов строк.")
    if not allow_at and ("@" in value or "`" in value or "|" in value):
        raise ValueError("Имя не может содержать @, ` или |.")
    return value


def application_embed(app) -> discord.Embed:
    status_map = {"pending": "Ожидает", "in_review": "На рассмотрении", "needs_info": "Уточнение",
                  "approving": "Одобрение выполняется", "rejecting": "Отказ выполняется",
                  "approved": "Одобрена", "rejected": "Отклонена", "withdrawn": "Отозвана",
                  "action_failed": "Требуется вмешательство"}
    e = discord.Embed(title=f"NewSquad  /  Заявка #{app.id:04d}", color=BRAND_GREEN)
    e.description = "Анкета на вступление в закрытое сообщество. Решение принимает администрация."
    e.add_field(name="Кандидат", value=f"<@{app.user_id}> (`{app.user_id}`)", inline=False)
    e.add_field(name="Discord", value=discord.utils.escape_markdown(app.username), inline=False)
    e.add_field(name="Имя", value=discord.utils.escape_markdown(app.name), inline=True)
    e.add_field(name="Пол", value="Женский" if app.gender == "female" else "Мужской", inline=True)
    e.add_field(name="Возраст", value=str(app.age), inline=True)
    e.add_field(name="Пригласил", value=discord.utils.escape_mentions(app.inviter), inline=False)
    e.add_field(name="Статус", value=status_map.get(app.status, app.status), inline=True)
    if app.reviewer_id:
        e.add_field(name="Рассматривает", value=f"<@{app.reviewer_id}>", inline=True)
    if app.decision_reason:
        e.add_field(name="Решение", value=discord.utils.escape_mentions(app.decision_reason[:500]), inline=False)
    if app.last_error:
        e.add_field(name="Ошибка выполнения", value=app.last_error[:500], inline=False)
    e.set_footer(text="NewSquad • Только для администрации • Данные анкеты")
    return e


class ApplicationForm(LoggedModal, title="NewSquad — заявка на вступление"):
    # Discord 2.6+: 4 поля на одном экране, Select внутри Label.
    name_field = discord.ui.Label(text="Как тебя зовут?", component=discord.ui.TextInput(min_length=1, max_length=24))
    gender_field = discord.ui.Label(text="Твой пол", component=discord.ui.Select(
        options=[discord.SelectOption(label="Мужской", value="male"),
                 discord.SelectOption(label="Женский", value="female")], min_values=1, max_values=1))
    age_field = discord.ui.Label(text="Сколько тебе полных лет?", component=discord.ui.TextInput(
        min_length=1, max_length=3, placeholder="Например: 20"))
    inviter_field = discord.ui.Label(text="Кто тебя пригласил?", component=discord.ui.TextInput(
        min_length=1, max_length=120, placeholder="Ник участника или «Не знаю»"))

    def __init__(self, cog: "Membership"):
        super().__init__(timeout=600)
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.user, discord.Member) or interaction.guild_id != self.cog.bot.config.guild_id:
            await interaction.response.send_message("Заявка доступна только на NewSquad.", ephemeral=True)
            return
        try:
            name = clean(self.name_field.component.value, limit=24)
            gender = self.gender_field.component.values[0]
            age_text = self.age_field.component.value.strip()
            if not re.fullmatch(r"[0-9]{1,3}", age_text):
                raise ValueError("Возраст нужно указать целым числом.")
            age = int(age_text)
            min_age = int(await self.cog.bot.database.get_setting(interaction.guild_id, "membership_min_age", 13))
            if not min_age <= age <= 120:
                raise ValueError(f"Допустимый возраст: {min_age}–120 лет.")
            inviter = clean(self.inviter_field.component.value, limit=120, allow_at=True)
            if gender not in ("male", "female"):
                raise ValueError("Выберите один из вариантов пола.")
            member_role_id = await self.cog.bot.database.get_setting(interaction.guild_id, "member_role_id")
            if member_role_id and any(r.id == member_role_id for r in interaction.user.roles):
                raise ValueError("Вы уже являетесь участником NewSquad.")
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            application = await store.submit(self.cog.bot.database, guild_id=interaction.guild_id,
                user_id=interaction.user.id, username=interaction.user.name, name=name,
                gender=gender, age=age, inviter=inviter)
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        try:
            await self.cog.publish(application)
            note = f"Заявка **#{application.id:04d}** отправлена. Ожидайте решения администрации."
        except (discord.HTTPException, RuntimeError):
            log.exception("Не удалось опубликовать заявку #%s", application.id)
            note = f"Заявка **#{application.id:04d}** сохранена, но её карточка пока не опубликована. Администрация сможет восстановить публикацию."
        await interaction.followup.send(note, ephemeral=True)


class ApplyPanel(LoggedView):
    def __init__(self, cog: "Membership"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Подать заявку", style=discord.ButtonStyle.success,
                       custom_id="newsqad:membership:apply")
    async def apply(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if interaction.guild_id != self.cog.bot.config.guild_id:
            await interaction.response.send_message("Панель только для NewSquad.", ephemeral=True)
            return
        active = await store.get_active(self.cog.bot.database, interaction.guild_id, interaction.user.id)
        if active:
            await interaction.response.send_message(f"У вас уже есть активная заявка #{active.id:04d}.", ephemeral=True)
            return
        await interaction.response.send_modal(ApplicationForm(self.cog))


class RejectForm(LoggedModal, title="Отклонение заявки"):
    reason = discord.ui.Label(text="Причина отказа", component=discord.ui.TextInput(
        style=discord.TextStyle.paragraph, min_length=3, max_length=400))

    def __init__(self, cog: "Membership", app_id: int):
        super().__init__(timeout=300)
        self.cog, self.app_id = cog, app_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await self.cog.decide(interaction, self.app_id, approve=False,
                                       reason=self.reason.component.value)
        await interaction.followup.send(result, ephemeral=True)


class ReviewView(LoggedView):
    def __init__(self, cog: "Membership", app_id: int):
        super().__init__(timeout=None)
        self.cog, self.app_id = cog, app_id
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.custom_id = f"newsqad:membership:{child.custom_id}:{app_id}"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not await self.cog.can_review(interaction):
            await interaction.response.send_message("Нет прав на рассмотрение заявок.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Взять на рассмотрение", style=discord.ButtonStyle.secondary, custom_id="claim", row=0)
    async def claim(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        updated = await store.transition(self.cog.bot.database, self.app_id,
            expected=("pending",), target="in_review", actor_id=interaction.user.id, reason="Взята в работу")
        await self.cog.refresh(self.app_id)
        await interaction.followup.send("Заявка закреплена за вами." if updated else "Уже находится в работе или завершена.", ephemeral=True)

    @discord.ui.button(label="Одобрить", style=discord.ButtonStyle.success, custom_id="approve", row=0)
    async def approve(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await self.cog.decide(interaction, self.app_id, approve=True, reason="Одобрено")
        await interaction.followup.send(result, ephemeral=True)

    @discord.ui.button(label="Отклонить", style=discord.ButtonStyle.danger, custom_id="reject", row=0)
    async def reject(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(RejectForm(self.cog, self.app_id))

    @discord.ui.button(label="История", style=discord.ButtonStyle.secondary, custom_id="history", row=1)
    async def history(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        app = await store.get_application(self.cog.bot.database, self.app_id)
        if app is None:
            await interaction.response.send_message("Заявка не найдена.", ephemeral=True)
            return
        history = await store.past_applications(self.cog.bot.database, app.guild_id, app.user_id)
        text = "\n".join(f"#{item.id:04d}: {item.status}" for item in history)
        await interaction.response.send_message(text or "История пуста.", ephemeral=True)


class Membership(commands.Cog):
    membership_commands = app_commands.Group(name="membership", description="Управление заявками NewSquad")
    application_commands = app_commands.Group(name="application", description="Ваша заявка в NewSquad")

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.add_view(ApplyPanel(self))
        for app in await store.active_reviews(self.bot.database, self.bot.config.guild_id):
            self.bot.add_view(ReviewView(self, app.id), message_id=app.review_message_id)

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        # Recover cards saved before a failed Discord send or crash.
        for app in await store.awaiting_publication(self.bot.database, self.bot.config.guild_id):
            try:
                await self.publish(app)
            except (discord.HTTPException, RuntimeError):
                log.exception("Не удалось восстановить карточку заявки %s", app.id)

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
            try:
                if role not in member.roles:
                    await member.add_roles(role, reason=f"[NSB-APPLICATION-{app_id:04d}] Одобрение")
                if app.gender == "female":
                    female_id = await self.bot.database.get_setting(app.guild_id, "female_role_id", FEMALE_ROLE_ID)
                    female = guild.get_role(female_id)
                    if female is None:
                        raise RuntimeError("Не найдена женская роль")
                    if female not in member.roles:
                        await member.add_roles(female, reason=f"[NSB-APPLICATION-{app_id:04d}] Женская роль")
                    await store.step(self.bot.database, app_id, "female_role_status", "done")
                else:
                    await store.step(self.bot.database, app_id, "female_role_status", "n_a")
                # Роль кандидата выдаёт другой бот. Удаляем её только ПОСЛЕ выдачи
                # Member (и женской роли, если требуется). При ошибке повторяем этот
                # шаг через /membership retry, не помечая заявку как одобренную.
                candidate_id = await self.bot.database.get_setting(app.guild_id, "candidate_role_id")
                if candidate_id == role.id:
                    raise RuntimeError("Роли кандидата и участника совпадают — исправьте /botsettings")
                if candidate_id:
                    candidate_role = guild.get_role(candidate_id)
                    if candidate_role is None:
                        raise RuntimeError("Настроенная роль кандидата отсутствует на сервере")
                    if candidate_role in member.roles:
                        await member.remove_roles(candidate_role,
                            reason=f"[NSB-APPLICATION-{app_id:04d}] Кандидат принят")
                await store.step(self.bot.database, app_id, "role_status", "done")
            except (discord.HTTPException, RuntimeError) as exc:
                await store.fail(self.bot.database, app_id, app.reviewer_id or 0, f"Ошибка выдачи роли: {exc}")
                return f"Роль не выдана полностью: {exc}. Исправьте права и используйте /membership retry."
            warning = ""
            try:
                new_nick = nickname(member.name, app.name)
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
                        color=BRAND_GREEN))
                    await store.step(self.bot.database, app_id, "dm_status", "done")
                except discord.HTTPException:
                    await store.step(self.bot.database, app_id, "dm_status", "failed")
            # Durable event: handlers of Welcome and Roles can recover after a restart.
            await self.bot.events.publish("membership.approved", {
                "guild_id": app.guild_id, "user_id": app.user_id, "application_id": app_id,
            }, event_id=f"membership-approved-{app_id}")
            await log_event(self.bot, channel_setting="membership_audit_channel_id",
                title=f"Заявка #{app_id:04d} одобрена", details=f"Кандидат: {app.user_id}\nМодератор: {app.reviewer_id}\n{warning}",
                actor_id=app.reviewer_id or 0, action="membership.approved", target_id=app.user_id)
            return "Кандидат принят." + warning
        # REJECT: DM before kick; unavailable DMs must not block kick.
        if app.dm_status == "pending":
            try:
                await member.send(embed=discord.Embed(
                    title="NewSquad · Решение по заявке",
                    description=(f"Заявка **#{app_id:04d}** отклонена.\n"
                                 f"**Причина:** {discord.utils.escape_mentions(app.decision_reason or 'Не указана')}\n\n"
                                 "Доступ к серверу будет закрыт."),
                    color=BRAND_GREEN), allowed_mentions=discord.AllowedMentions.none())
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
            color=BRAND_GREEN), view=ApplyPanel(self))
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
        keys = ("member_role_id", "candidate_role_id", "female_role_id", "reviewer_role_id",
                "application_channel_id", "review_channel_id", "membership_audit_channel_id")
        lines = [f"{key}: {await self.bot.database.get_setting(interaction.guild_id, key, FEMALE_ROLE_ID if key == 'female_role_id' else 'не настроено')}" for key in keys]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

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
