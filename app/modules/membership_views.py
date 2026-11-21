"""Membership Discord panels, dynamic application forms and newcomer guide."""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

import discord

from app.services import applications as store
from app.services.membership_workflow import form_types, selected_form
from app.ui.theme import LoggedModal, LoggedView, panel_card

if TYPE_CHECKING:
    from app.modules.membership import Membership

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
    e = panel_card(
        f"NewSquad  /  Заявка #{app.id:04d}",
        "Анкета кандидата. Для рассмотрения используйте кнопки под сообщением.",
        ("Кандидат", f"<@{app.user_id}>  ·  **{discord.utils.escape_markdown(app.username)}**"),
        ("Анкета", f"**Имя:** {discord.utils.escape_markdown(app.name)}\n"
                   f"**Пол:** {'Женский' if app.gender == 'female' else 'Мужской'}\n"
                   f"**Возраст:** {app.age}\n"
                   f"**Пригласил:** {discord.utils.escape_mentions(app.inviter)}"),
        footer="NEW SQUAD  •  Приватная анкета для администрации",
    )
    if getattr(app, "form_key", "standard") != "standard":
        e.add_field(name="Тип заявки", value=discord.utils.escape_mentions(app.form_key), inline=True)
    if getattr(app, "extra_answer", None):
        e.add_field(name="Ответ по дополнительному вопросу",
                    value=discord.utils.escape_mentions(app.extra_answer[:950]), inline=False)
    if getattr(app, "followup_answer", None):
        e.add_field(name="Ответ на уточнение",
                    value=discord.utils.escape_mentions(app.followup_answer[:900]), inline=False)
    e.add_field(name="Статус заявки", value=status_map.get(app.status, app.status), inline=True)
    e.add_field(name="Подана", value=f"<t:{app.created_at}:F>", inline=True)
    if app.reviewer_id:
        e.add_field(name="Рассматривает", value=f"<@{app.reviewer_id}>", inline=True)
    if app.decision_reason:
        e.add_field(name="Решение", value=discord.utils.escape_mentions(app.decision_reason[:500]), inline=False)
    if app.last_error:
        e.add_field(name="Не завершено", value=discord.utils.escape_mentions(app.last_error[:500]), inline=False)
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

    def __init__(self, cog: "Membership", *, form_key: str = "standard", question: str = ""):
        super().__init__(timeout=600)
        self.cog = cog
        self.form_key = form_key
        self.extra_field = None
        if question:
            self.extra_field = discord.ui.Label(
                text=question, component=discord.ui.TextInput(
                    style=discord.TextStyle.paragraph, min_length=2, max_length=240))
            self.add_item(self.extra_field)

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
            candidate_role_id = await self.cog.bot.database.get_setting(
                interaction.guild_id, "candidate_role_id",
            )
            if not candidate_role_id or interaction.guild.get_role(candidate_role_id) is None:
                raise ValueError("Роль кандидата не настроена: сообщите администрации.")
            if not any(role.id == candidate_role_id for role in interaction.user.roles):
                raise ValueError("Подать заявку может только кандидат NewSquad.")
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            application = await store.submit(self.cog.bot.database, guild_id=interaction.guild_id,
                user_id=interaction.user.id, username=interaction.user.name, name=name,
                gender=gender, age=age, inviter=inviter, form_key=self.form_key,
                extra_answer=self.extra_field.component.value if self.extra_field else "")
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        try:
            await self.cog.publish(application)
            await self.cog.schedule_review_reminder(application)
            note = f"Заявка **#{application.id:04d}** отправлена. Ожидайте решения администрации."
        except (discord.HTTPException, RuntimeError):
            log.exception("Не удалось опубликовать заявку #%s", application.id)
            note = f"Заявка **#{application.id:04d}** сохранена, но её карточка пока не опубликована. Администрация сможет восстановить публикацию."
        await interaction.followup.send(note, ephemeral=True)


class ApplyPanel(LoggedView):
    def __init__(self, cog: "Membership"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Подать заявку", style=discord.ButtonStyle.primary,
                       custom_id="newsqad:membership:apply")
    async def apply(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if interaction.guild_id != self.cog.bot.config.guild_id:
            await interaction.response.send_message("Панель только для NewSquad.", ephemeral=True)
            return
        active = await store.get_active(self.cog.bot.database, interaction.guild_id, interaction.user.id)
        if active:
            await interaction.response.send_message(f"У вас уже есть активная заявка #{active.id:04d}.", ephemeral=True)
            return
        variants = await form_types(self.cog.bot.database, interaction.guild_id, enabled_only=True)
        available = [row for row in variants if row["enabled"]]
        if len(available) == 1:
            await interaction.response.send_modal(ApplicationForm(self.cog))
        else:
            await interaction.response.send_message(
                "Выберите подходящий тип заявки:", ephemeral=True,
                view=FormTypeSelect(self.cog, available))


class FormTypeSelect(discord.ui.View):
    def __init__(self, cog: "Membership", forms: list[dict]):
        super().__init__(timeout=180)
        choices = [discord.SelectOption(label=item["title"][:100], value=item["key"])
                   for item in forms[:9]]
        select = discord.ui.Select(placeholder="Тип заявки", options=choices)

        async def choose(interaction: discord.Interaction) -> None:
            if interaction.guild_id != cog.bot.config.guild_id:
                await interaction.response.send_message("Недоступный сервер.", ephemeral=True)
                return
            form = await selected_form(cog.bot.database, interaction.guild_id, select.values[0])
            if form is None:
                await interaction.response.send_message("Этот тип заявки отключён.", ephemeral=True)
                return
            await interaction.response.send_modal(
                ApplicationForm(cog, form_key=form["key"], question=form["question"]))
        select.callback = choose
        self.add_item(select)


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


class FollowupQuestion(LoggedModal, title="Уточнение по заявке"):
    question = discord.ui.TextInput(
        label="Вопрос кандидату", style=discord.TextStyle.paragraph,
        min_length=5, max_length=500)

    def __init__(self, cog: "Membership", app_id: int):
        super().__init__(timeout=300)
        self.cog, self.app_id = cog, app_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await self.cog.request_info(interaction, self.app_id, str(self.question))
        await interaction.followup.send(result, ephemeral=True)


class ReviewView(LoggedView):
    def __init__(self, cog: "Membership", app_id: int):
        super().__init__(timeout=None)
        self.cog, self.app_id = cog, app_id
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.custom_id = f"newsqad:membership:{child.custom_id}:{app_id}"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not await super().interaction_check(interaction):
            return False
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

    @discord.ui.button(label="Одобрить", style=discord.ButtonStyle.primary, custom_id="approve", row=0)
    async def approve(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await self.cog.decide(interaction, self.app_id, approve=True, reason="Одобрено")
        await interaction.followup.send(result, ephemeral=True)

    @discord.ui.button(label="Отклонить", style=discord.ButtonStyle.danger, custom_id="reject", row=0)
    async def reject(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(RejectForm(self.cog, self.app_id))

    @discord.ui.button(label="История", style=discord.ButtonStyle.secondary, custom_id="history", row=1)
    async def history(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        items = await store.timeline(self.cog.bot.database, interaction.guild_id, self.app_id)
        lines = [f"<t:{e.created_at}:f> · <@{e.actor_id}> · {e.old_status} → {e.new_status}\n"
                 f"{discord.utils.escape_mentions(e.reason[:220])}" for e in items]
        await interaction.response.send_message("\n\n".join(lines)[:1900] or "История пуста.",
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label="Уточнить", style=discord.ButtonStyle.secondary, custom_id="ask", row=1)
    async def ask(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(FollowupQuestion(self.cog, self.app_id))


class NewMemberGuide(discord.ui.View):
    """Ephemeral onboarding navigator; visible channels checked against Discord ACL."""
    def __init__(self, bot, owner_id: int):
        super().__init__(timeout=300)
        self.bot = bot
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Эта панель принадлежит другому участнику.", ephemeral=True)
            return False
        return True

    async def _configuration(self, interaction: discord.Interaction):
        from app.web.v21 import ONBOARDING_DEFAULT

        return await self.bot.database.get_setting(
            interaction.guild_id, "membership_onboarding", ONBOARDING_DEFAULT)

    @discord.ui.button(label="Правила", style=discord.ButtonStyle.primary)
    async def rules(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        cfg = await self._configuration(interaction)
        await interaction.response.send_message(
            discord.utils.escape_mentions(cfg["rules"]), ephemeral=True)

    @discord.ui.button(label="Каналы", style=discord.ButtonStyle.secondary)
    async def channels(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        cfg = await self._configuration(interaction)
        channels = []
        for channel_id in cfg["channel_ids"]:
            channel = interaction.guild.get_channel(channel_id)
            if channel and channel.permissions_for(interaction.user).view_channel:
                channels.append(channel.mention)
        await interaction.response.send_message(
            "Доступные каналы: " + (", ".join(channels) if channels else
            "Администратор пока не настроил список."), ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label="Первые задания", style=discord.ButtonStyle.secondary)
    async def tasks(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        cfg = await self._configuration(interaction)
        await interaction.response.send_message(
            "**Начните здесь:**\\n" + "\\n".join(
                f"{i}. {discord.utils.escape_mentions(item)}"
                for i, item in enumerate(cfg["tasks"], 1)), ephemeral=True)


