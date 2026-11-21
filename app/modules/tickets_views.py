"""Discord ticket modals, controls and support rating components."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

from app.models import Ticket, TicketCategory
from app.services import support_quality, tickets
from app.services.membership_access import approved_member
from app.ui.theme import LoggedModal, LoggedView, card, notice, panel_card

if TYPE_CHECKING:
    from app.modules.tickets import Tickets

log = logging.getLogger(__name__)


async def report_ticket_ui_error(interaction: discord.Interaction, error: Exception, operation: str) -> None:
    """Логировать ошибки компонентов, не оставляя участника без ответа."""
    log.error("Сбой интерфейса тикетов: %s; interaction=%s; user=%s",
              operation, interaction.id, interaction.user.id,
              exc_info=(type(error), error, error.__traceback__))
    try:
        message = "Ошибка обработки обращения. Повторите попытку или сообщите администрации."
        if interaction.response.is_done():
            await interaction.followup.send(embed=notice(message, kind="error"), ephemeral=True)
        else:
            await interaction.response.send_message(embed=notice(message, kind="error"), ephemeral=True)
    except discord.HTTPException:
        log.exception("Не удалось отправить сообщение об ошибке тикета interaction=%s", interaction.id)


def ticket_embed(ticket: Ticket, category: TicketCategory | None = None) -> discord.Embed:
    labels = {"creating": "Создаётся", "open": "Открыт", "closing": "Закрывается",
              "closed": "Закрыт", "failed": "Ошибка"}
    e = panel_card(
        f"NewSquad  /  Обращение #{ticket.id:05d}",
        f"**{discord.utils.escape_mentions(ticket.subject[:180])}**",
        ("Описание",
         discord.utils.escape_mentions(ticket.description[:1500])),
        footer="NEW SQUAD  •  Поддержка  /  Приватное обращение",
    )
    e.add_field(name="Категория", value=discord.utils.escape_mentions(category.title) if category
                else f"#{ticket.category_id}", inline=True)
    e.add_field(name="Статус", value=labels.get(ticket.status, ticket.status), inline=True)
    e.add_field(name="Приоритет", value={
        "low": "Низкий", "normal": "Обычный", "high": "Высокий", "urgent": "Срочный",
    }.get(getattr(ticket, "priority", "normal"), getattr(ticket, "priority", "normal")), inline=True)
    e.add_field(name="Автор", value=f"<@{ticket.user_id}>", inline=True)
    e.add_field(name="Ответственный", value=f"<@{ticket.assignee_id}>"
                if ticket.assignee_id else "Не назначен", inline=True)
    if ticket.close_reason:
        e.add_field(name="Причина закрытия",
                    value=discord.utils.escape_mentions(ticket.close_reason[:500]), inline=False)
    return e


class TicketOpenView(LoggedView):
    def __init__(self, cog: "Tickets"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Открыть обращение", emoji="📩", style=discord.ButtonStyle.primary,
                       custom_id="newsqad:tickets:open")
    async def open(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if interaction.guild_id != self.cog.bot.config.guild_id or not interaction.message:
            await interaction.response.send_message("Панель доступна только на NewSquad.", ephemeral=True)
            return
        # Не задерживаем ACK из-за запросов к SQLite: Discord ждёт ответ на interaction до 3 секунд.
        await interaction.response.defer(ephemeral=True, thinking=True)
        if not await tickets.panel_active(self.cog.bot.database, interaction.guild_id, interaction.message.id):
            await interaction.followup.send("Эта панель обращений неактивна.", ephemeral=True)
            return
        if not await self.cog.bot.database.get_setting(interaction.guild_id, "tickets_enabled", True):
            await interaction.followup.send("Поддержка временно недоступна.", ephemeral=True)
            return
        parent_id = await self.cog.bot.database.get_setting(interaction.guild_id, "ticket_category_channel_id")
        parent = interaction.guild.get_channel(parent_id) if interaction.guild and parent_id else None
        if not isinstance(parent, discord.CategoryChannel):
            await interaction.followup.send(
                "Тикеты пока не настроены: администрация должна выбрать категорию Discord "
                "для приватных каналов в `/botsettings` → «Тикеты» → «Категория каналов» "
                "(или командой `/tickets location`).",
                ephemeral=True,
            )
            return
        categories = await tickets.categories(self.cog.bot.database, interaction.guild_id)
        if not categories:
            await interaction.followup.send("Категории пока не настроены.", ephemeral=True)
            return
        if not isinstance(interaction.user, discord.Member):
            await interaction.followup.send("Завершите проверку правил сервера.", ephemeral=True)
            return
        member_role = await self.cog.bot.database.get_setting(interaction.guild_id, "candidate_role_id")
        if not await approved_member(self.cog.bot.database, interaction.user, member_role):
            categories = [c for c in categories if c.candidate_allowed]
        if not categories:
            await interaction.followup.send("Тикеты доступны после принятия в NewSquad.", ephemeral=True)
            return
        await interaction.followup.send("Выберите категорию обращения:",
                                        view=TicketCategoryView(self.cog, categories[:25]), ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        await report_ticket_ui_error(interaction, error, "кнопка открытия тикета")


class TicketCategorySelect(discord.ui.Select):
    def __init__(self, cog: "Tickets", categories: list[TicketCategory]):
        self.cog = cog
        super().__init__(placeholder="Выберите категорию", min_values=1, max_values=1,
            options=[discord.SelectOption(label=c.title[:90], description=c.description[:100],
                                          value=str(c.id)) for c in categories])

    async def callback(self, interaction: discord.Interaction) -> None:
        # Модальное окно должно стать ПЕРВЫМ ответом interaction.
        # Нельзя делать запросы к БД до send_modal: проверка актуальности
        # категории и членства выполняется повторно в Tickets.create().
        category_id = int(self.values[0])
        await interaction.response.send_modal(NewTicketModal(self.cog, category_id))


class TicketCategoryView(LoggedView):
    def __init__(self, cog: "Tickets", categories: list[TicketCategory]):
        super().__init__(timeout=180)
        self.add_item(TicketCategorySelect(cog, categories))

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        await report_ticket_ui_error(interaction, error, "выбор категории тикета")


class NewTicketModal(LoggedModal, title="NewSquad — обращение в поддержку"):
    subject = discord.ui.TextInput(label="Тема обращения", min_length=3, max_length=100)
    description = discord.ui.TextInput(label="Опишите ситуацию", style=discord.TextStyle.paragraph,
                                       min_length=3, max_length=1500)

    def __init__(self, cog: "Tickets", category_id: int):
        # Явный ID даёт возможность диагностировать доставку отправленной формы.
        super().__init__(timeout=600, custom_id=f"newsqad:tickets:create:{category_id}")
        self.cog, self.category_id = cog, category_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        log.info("Получена заявка в поддержку: interaction=%s user=%s category=%s",
                 interaction.id, interaction.user.id, self.category_id)
        # Сначала подтверждаем форму, только потом создаём канал и пишем в БД.
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            result = await self.cog.create(interaction, self.category_id,
                                           self.subject.value, self.description.value)
        except Exception:
            log.exception("Неожиданная ошибка создания тикета: interaction=%s user=%s category=%s",
                          interaction.id, interaction.user.id, self.category_id)
            result = "Не удалось создать обращение из-за внутренней ошибки. Сообщите администрации."
        try:
            await interaction.followup.send(result, ephemeral=True)
        except discord.HTTPException:
            log.exception("Не удалось доставить результат создания тикета interaction=%s", interaction.id)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await report_ticket_ui_error(interaction, error, "отправка формы тикета")


class ConfirmTicketDeletion(LoggedView):
    def __init__(self, cog: "Tickets", ticket_id: int, owner_id: int):
        super().__init__(timeout=90)
        self.cog, self.ticket_id, self.owner_id = cog, ticket_id, owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not await super().interaction_check(interaction):
            return False
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(embed=notice("Это подтверждение открыто другим участником.",
                kind="warning"), ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Удалить обращение", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def confirm(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await self.cog.close_ticket(interaction, self.ticket_id, "Закрыто")
        try:
            await interaction.followup.send(embed=notice(result, kind="success" if result.startswith("Обращение закрыто")
                else "warning"), ephemeral=True)
        except discord.HTTPException:
            # Канал исходного взаимодействия мог быть удалён раньше отправки followup.
            log.info("Ответ после удаления канала недоступен, ticket_id=%s interaction_id=%s",
                     self.ticket_id, interaction.id)

    @discord.ui.button(label="Отмена", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(embed=notice("Закрытие отменено."), view=None)


class TicketActions(LoggedView):
    def __init__(self, cog: "Tickets", ticket_id: int):
        super().__init__(timeout=None)
        self.cog, self.ticket_id = cog, ticket_id
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.custom_id = f"newsqad:tickets:{item.custom_id}:{ticket_id}"

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        await report_ticket_ui_error(interaction, error, f"управление тикетом #{self.ticket_id}")

    @discord.ui.button(label="Взять в работу", style=discord.ButtonStyle.primary, custom_id="claim")
    async def claim(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await self.cog.staff(interaction):
            await interaction.response.send_message("Нет прав на обработку обращений.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        ok = await tickets.claim(self.cog.bot.database, interaction.guild_id, self.ticket_id, interaction.user.id)
        if ok:
            await self.cog.refresh(self.ticket_id)
            await self.cog.audit("Тикет принят в работу", self.ticket_id, interaction.user.id)
        await interaction.followup.send("Вы назначены ответственным." if ok else "Тикет уже назначен или закрыт.", ephemeral=True)

    @discord.ui.button(label="Закрыть и удалить", style=discord.ButtonStyle.danger, custom_id="close")
    async def close(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        ticket = await tickets.get(self.cog.bot.database, interaction.guild_id, self.ticket_id)
        if not ticket or (interaction.user.id != ticket.user_id and not await self.cog.staff(interaction, ticket)):
            await interaction.response.send_message("Недостаточно прав.", ephemeral=True)
            return
        await interaction.response.send_message(
            embed=card("Закрыть обращение?",
                       "Приватный канал будет **удалён безвозвратно**. "
                       "Переписка, журнал и данные закрытого тикета не сохраняются."),
            view=ConfirmTicketDeletion(self.cog, self.ticket_id, interaction.user.id), ephemeral=True,
        )


class SupportSurveyView(discord.ui.View):
    """Persistent rating buttons, usable in DM even after bot restart."""

    def __init__(self, bot, survey_id: int, owner_id: int):
        super().__init__(timeout=None)
        self.bot, self.survey_id, self.owner_id = bot, survey_id, owner_id
        for rating in range(1, 6):
            button = discord.ui.Button(
                label=f"{rating} ★", custom_id=f"newsqad:support:rate:{survey_id}:{rating}",
                style=discord.ButtonStyle.success if rating >= 4 else discord.ButtonStyle.secondary,
            )

            async def on_rating(interaction: discord.Interaction, *, score=rating) -> None:
                if interaction.user.id != self.owner_id:
                    await interaction.response.send_message("Оценка предназначена другому участнику.",
                                                            ephemeral=True)
                    return
                ok = await support_quality.rate(
                    self.bot.database, self.bot.config.guild_id, self.survey_id,
                    interaction.user.id, score)
                if not ok:
                    await interaction.response.send_message(
                        "Оценка уже получена или срок опроса истёк.", ephemeral=True)
                    return
                await interaction.response.edit_message(
                    content=f"Спасибо за оценку: {score} из 5. Опрос завершён.", view=None)

            button.callback = on_rating
            self.add_item(button)


