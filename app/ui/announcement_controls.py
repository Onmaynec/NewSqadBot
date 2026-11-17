"""Приватный предпросмотр объявления: отправка, редактирование, расписание."""
from __future__ import annotations

import discord

from app.core.permissions import can_manage_settings
from app.services import announcements as store
from app.ui.theme import NO_MENTIONS, LoggedModal, LoggedView, card, notice


def preview_embed(item) -> discord.Embed:
    if item.kind == "embed":
        from app.modules.announcements import build_announcement
        embed = build_announcement("embed", item.title, item.body, item.color)["embed"]
        embed.set_footer(text=f"ПРЕДПРОСМОТР • Объявление #{item.id} • {store.STATUS_LABELS[item.status]}")
    else:
        embed = card(
            f"NewSquad / Предпросмотр текста #{item.id}",
            f"**{discord.utils.escape_mentions(item.title)}**\n\n"
            f"{discord.utils.escape_mentions(item.body)}",
        )
    embed.add_field(name="Публикация", value=f"<#{item.channel_id}>", inline=True)
    if item.due_at:
        embed.add_field(name="Время", value=f"<t:{item.due_at}:F>", inline=True)
    return embed


class EditAnnouncementModal(LoggedModal):
    def __init__(self, cog, item):
        super().__init__(title=f"Изменить объявление #{item.id}", timeout=600)
        self.cog = cog
        self.number = item.id
        self.headline = discord.ui.TextInput(
            style=discord.TextStyle.short, default=item.title, min_length=1, max_length=120,
        )
        self.body = discord.ui.TextInput(
            style=discord.TextStyle.paragraph, default=item.body,
            min_length=1, max_length=3500 if item.kind == "embed" else 1750,
        )
        self.add_item(discord.ui.Label(text="Заголовок", component=self.headline))
        self.add_item(discord.ui.Label(text="Текст объявления", component=self.body))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.edit_record(
            interaction, number=self.number, title=self.headline.value, body=self.body.value,
        )


class ScheduleAnnouncementModal(LoggedModal):
    def __init__(self, cog, number: int):
        super().__init__(title=f"Отложить публикацию #{number}", timeout=300)
        self.cog = cog
        self.number = number
        self.date = discord.ui.TextInput(
            style=discord.TextStyle.short, min_length=16, max_length=16,
            placeholder="2026-12-01 19:30",
        )
        self.add_item(discord.ui.Label(
            text=f"Дата и время ({cog.bot.config.timezone})", component=self.date,
        ))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.schedule_record(interaction, self.number, self.date.value)


class AnnouncementPreviewView(LoggedView):
    def __init__(self, cog, item, owner_id: int):
        super().__init__(timeout=600)
        self.cog, self.item, self.owner_id = cog, item, owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not await super().interaction_check(interaction):
            return False
        if (interaction.user.id != self.owner_id
                or not can_manage_settings(interaction, self.cog.bot.config.owner_ids)):
            await interaction.response.send_message(
                embed=notice("Только автор предпросмотра с правами администратора.", kind="warning"),
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Опубликовать", style=discord.ButtonStyle.primary, emoji="📢")
    async def send(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.cog.send_record(interaction, self.item.id)

    @discord.ui.button(label="Изменить", style=discord.ButtonStyle.secondary, emoji="✏️")
    async def edit(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        item = await store.get(self.cog.bot.database, self.cog.bot.config.guild_id, self.item.id)
        if item is None or item.status not in ("draft", "failed"):
            await interaction.response.send_message(
                embed=notice("Этот черновик уже нельзя редактировать.", kind="warning"),
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(EditAnnouncementModal(self.cog, item))

    @discord.ui.button(label="Отложить", style=discord.ButtonStyle.secondary, emoji="🕒")
    async def schedule(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(ScheduleAnnouncementModal(self.cog, self.item.id))

    @discord.ui.button(label="Отменить", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.cog.cancel_record(interaction, self.item.id)

    @discord.ui.button(label="Сохранено", style=discord.ButtonStyle.secondary, disabled=True)
    async def saved(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        return


async def display_preview(interaction: discord.Interaction, cog, item) -> None:
    await interaction.response.send_message(
        embed=preview_embed(item),
        view=AnnouncementPreviewView(cog, item, interaction.user.id),
        ephemeral=True,
        allowed_mentions=NO_MENTIONS,
    )
