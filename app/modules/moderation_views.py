"""Discord moderation case modals, user search and confirmation controls."""
from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

import discord

from app.services import moderation_cases as cases_store
from app.ui.theme import LoggedModal, LoggedView, notice, panel_card

if TYPE_CHECKING:
    from app.modules.moderation import Moderation


class CaseOperationForm(LoggedModal):
    value = discord.ui.TextInput(label="Значение или причина", min_length=3, max_length=400)

    def __init__(self, cog: Moderation, number: int, operation: str, owner_id: int):
        title = {"revoke": "Подтвердить отмену наказания",
                 "reason": "Изменение причины", "duration": "Изменение срока"}[operation]
        super().__init__(title=title, timeout=300)
        self.cog, self.number, self.operation, self.owner_id = cog, number, operation, owner_id
        if operation == "duration":
            self.value.label = "Срок: 10m, 2h, 7d или per"
            self.value.min_length = 2

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Только инициатор операции.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await self.cog._case_change(
            interaction, self.number, action=self.operation, value=str(self.value))
        await interaction.followup.send(embed=notice(result), ephemeral=True)


class CaseActions(LoggedView):
    def __init__(self, cog: Moderation, number: int, owner_id: int):
        super().__init__(timeout=300)
        self.cog, self.number, self.owner_id = cog, number, owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not await super().interaction_check(interaction):
            return False
        if interaction.user.id != self.owner_id or not await self.cog.can_moderate(interaction):
            await interaction.response.send_message("Панель принадлежит другому модератору.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Отменить наказание", style=discord.ButtonStyle.danger)
    async def revoke(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(CaseOperationForm(
            self.cog, self.number, "revoke", self.owner_id))

    @discord.ui.button(label="Изменить причину", style=discord.ButtonStyle.secondary)
    async def edit_reason(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(CaseOperationForm(
            self.cog, self.number, "reason", self.owner_id))

    @discord.ui.button(label="Изменить срок", style=discord.ButtonStyle.secondary)
    async def duration(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(CaseOperationForm(
            self.cog, self.number, "duration", self.owner_id))

    @discord.ui.button(label="Журнал действий", style=discord.ButtonStyle.secondary)
    async def actions(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        rows = await cases_store.actions(self.cog.bot.database, interaction.guild_id, self.number)
        lines = [f"<t:{row.created_at}:f> · <@{row.actor_id}> · "
                 f"**{row.action}** · {discord.utils.escape_mentions(row.details[:200])}"
                 for row in rows]
        await interaction.response.send_message(embed=panel_card(
            f"NewSquad / История дела #{self.number:05d}",
            "\n".join(lines)[:3600] if lines else "Изменений нет."),
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


class CaseLookupForm(LoggedModal, title="Поиск дела модерации"):
    number = discord.ui.TextInput(label="Номер дела", min_length=1, max_length=10)
    def __init__(self, cog: Moderation, owner_id: int):
        super().__init__(timeout=300)
        self.cog, self.owner_id = cog, owner_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.owner_id or not await self.cog.can_moderate(interaction):
            await interaction.response.send_message("Нет доступа.", ephemeral=True)
            return
        if not str(self.number).isascii() or not str(self.number).isdecimal():
            await interaction.response.send_message("Введите цифровой номер дела.", ephemeral=True)
            return
        number = int(str(self.number))
        rows = await cases_store.find(self.cog.bot.database, interaction.guild_id, number=number)
        if not rows:
            await interaction.response.send_message("Дело не найдено.", ephemeral=True)
            return
        await interaction.response.send_message(embed=self.cog.case_card(rows[0]),
            view=CaseActions(self.cog, number, self.owner_id), ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none())


class MemberCasesForm(LoggedModal, title="История пользователя"):
    user_id = discord.ui.TextInput(label="Discord ID участника", min_length=15, max_length=22)
    def __init__(self, cog: Moderation, owner_id: int):
        super().__init__(timeout=300)
        self.cog, self.owner_id = cog, owner_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.owner_id or not await self.cog.can_moderate(interaction):
            await interaction.response.send_message("Нет доступа.", ephemeral=True)
            return
        if not str(self.user_id).isascii() or not str(self.user_id).isdecimal():
            await interaction.response.send_message("Введите цифровой Discord ID.", ephemeral=True)
            return
        rows = await cases_store.find(self.cog.bot.database, interaction.guild_id,
                                      user_id=int(str(self.user_id)), limit=25)
        lines = [f"#{c.id:05d} · {c.kind} · {c.status}: "
                 f"{discord.utils.escape_mentions(c.reason[:70])}" for c in rows]
        await interaction.response.send_message(embed=panel_card(
            "NewSquad / История участника",
            "\n".join(lines)[:3500] if lines else "Дел нет."), ephemeral=True)


class ModerationPanel(LoggedView):
    def __init__(self, cog: Moderation, owner_id: int):
        super().__init__(timeout=300)
        self.cog, self.owner_id = cog, owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not await super().interaction_check(interaction):
            return False
        if interaction.user.id != self.owner_id or not await self.cog.can_moderate(interaction):
            await interaction.response.send_message("Эта панель открыта другим модератором.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Активные дела", style=discord.ButtonStyle.primary)
    async def active(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        rows = await cases_store.find(self.cog.bot.database, interaction.guild_id, status="active", limit=20)
        lines = [f"#{r.id:05d} · {r.kind} · <@{r.user_id}> · {r.status}"
                 for r in rows]
        await interaction.response.send_message(embed=panel_card(
            "NewSquad / Активные дела", "\n".join(lines) if lines else "Активных дел нет."),
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label="Найти дело", style=discord.ButtonStyle.secondary)
    async def lookup(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(CaseLookupForm(self.cog, self.owner_id))

    @discord.ui.button(label="История участника", style=discord.ButtonStyle.secondary)
    async def history(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(MemberCasesForm(self.cog, self.owner_id))

    @discord.ui.button(label="Предупреждения", style=discord.ButtonStyle.secondary)
    async def warnings(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        rows = await cases_store.find(self.cog.bot.database, interaction.guild_id,
                                      kind="warn", status="active", limit=20)
        lines = [f"#{r.id:05d} · <@{r.user_id}> · "
                 f"{discord.utils.escape_mentions(r.reason[:80])}" for r in rows]
        await interaction.response.send_message(embed=panel_card(
            "NewSquad / Активные предупреждения",
            "\n".join(lines) if lines else "Предупреждений нет."), ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none())


class ConfirmClear(LoggedView):
    def __init__(self, cog: Moderation, moderator_id: int, user: discord.User,
                 channel: discord.TextChannel, count: int, cutoff: dt.datetime | None, reason: str):
        super().__init__(timeout=90)
        self.cog, self.moderator_id, self.user, self.channel = cog, moderator_id, user, channel
        self.count, self.cutoff, self.reason = count, cutoff, reason

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not await super().interaction_check(interaction):
            return False
        if interaction.user.id != self.moderator_id or not await self.cog.can_moderate(interaction):
            await interaction.response.send_message("Подтвердить может только модератор, запустивший команду.",
                                                    ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Удалить", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="Удаление выполняется...", view=self)
        result = await self.cog.execute_clear(interaction, user=self.user, channel=self.channel,
                                               count=self.count, cutoff=self.cutoff, reason=self.reason)
        await interaction.followup.send(result, ephemeral=True)
        self.stop()

    @discord.ui.button(label="Отмена", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="Очистка отменена.", view=self)
        self.stop()

