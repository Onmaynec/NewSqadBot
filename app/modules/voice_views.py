"""Discord voice room controls, invitation selector, modals and embeds."""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

import discord

from app.core.permissions import can_manage_settings
from app.services import voice as store
from app.ui.theme import LoggedModal, LoggedView, notice, panel_card

if TYPE_CHECKING:
    from app.modules.voice import Voice


def room_name(raw: str) -> str:
    """Не разрешаем управляющие символы и упоминания в имени создаваемого канала."""
    name = re.sub(r"[\r\n\t\x00-\x1f\x7f]", " ", raw)
    name = re.sub(r"\s+", " ", name).strip()
    if not 2 <= len(name) <= 90 or "@everyone" in name.lower() or "@here" in name.lower():
        raise ValueError("Название должно содержать 2–90 символов без массовых упоминаний.")
    return name


def describe_room(room, channel: discord.VoiceChannel | None = None) -> discord.Embed:
    count = len(channel.members) if channel else 0
    embed = panel_card(
        "NewSquad  /  Управление комнатой",
        f"**{discord.utils.escape_markdown(room.name)}**",
        ("Основная информация",
         f"**Канал:** <#{room.channel_id}>\n"
         f"**Владелец:** <@{room.owner_id}>\n"
         f"**Участников:** {count} / {room.user_limit or 'без лимита'}"),
        ("Доступ и приватность",
         f"**Подключение:** {'закрыто' if room.locked else 'свободное'}\n"
         f"**Видимость:** {'скрытая' if room.hidden else 'публичная'}"),
        ("Управление",
         "Используйте кнопки для изменения комнаты.\n"
         "Приглашения и ограничения: `/voice allow` · `/voice deny`."),
        footer="NEW SQUAD  •  Комната удаляется, когда остаётся пустой",
    )
    return embed


class RenameRoomModal(LoggedModal, title="Имя голосовой комнаты"):
    name = discord.ui.TextInput(label="Новое название", min_length=2, max_length=90)

    def __init__(self, cog: "Voice", channel_id: int):
        super().__init__(timeout=300)
        self.cog, self.channel_id = cog, channel_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await self.cog.rename_room(interaction, self.channel_id, self.name.value)
        await interaction.followup.send(embed=notice(result, kind="success" if result.startswith("Готово") else "warning"),
                                        ephemeral=True)


class LimitRoomModal(LoggedModal, title="Лимит голосовой комнаты"):
    amount = discord.ui.TextInput(label="Мест (0 — без лимита)", min_length=1, max_length=2, placeholder="0")

    def __init__(self, cog: "Voice", channel_id: int):
        super().__init__(timeout=300)
        self.cog, self.channel_id = cog, channel_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            amount = int(self.amount.value)
        except ValueError:
            result = "Укажите число от 0 до 99."
        else:
            result = await self.cog.limit_room(interaction, self.channel_id, amount)
        await interaction.followup.send(embed=notice(result, kind="success" if result.startswith("Готово") else "warning"),
                                        ephemeral=True)


class CloseRoomConfirm(LoggedView):
    def __init__(self, cog: "Voice", channel_id: int):
        super().__init__(timeout=90)
        self.cog, self.channel_id = cog, channel_id

    @discord.ui.button(label="Удалить комнату", style=discord.ButtonStyle.danger)
    async def accept(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await self.cog.delete_room(interaction, self.channel_id)
        await interaction.followup.send(embed=notice(result, kind="success" if result.startswith("Готово") else "warning"),
                                        ephemeral=True)

    @discord.ui.button(label="Отмена", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(embed=notice("Удаление отменено."), view=None)


class RoomControls(LoggedView):
    def __init__(self, cog: "Voice", channel_id: int):
        super().__init__(timeout=600)
        self.cog, self.channel_id = cog, channel_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not await super().interaction_check(interaction):
            return False
        room = await store.by_channel(self.cog.bot.database, interaction.guild_id or 0, self.channel_id)
        admin = can_manage_settings(interaction, self.cog.bot.config.owner_ids)
        owner = (room is not None and room.owner_id == interaction.user.id
                 and isinstance(interaction.user, discord.Member)
                 and await self.cog._approved(interaction.user))
        if room is None or not (owner or admin):
            await interaction.response.send_message(embed=notice("Эти кнопки доступны только владельцу комнаты.",
                kind="warning"), ephemeral=True)
            return False
        return True

    async def _toggle(self, interaction: discord.Interaction, field: str) -> None:
        await interaction.response.defer()
        room = await store.by_channel(self.cog.bot.database, interaction.guild_id, self.channel_id)
        if room is None:
            await interaction.followup.send(embed=notice("Комната уже удалена.", kind="warning"), ephemeral=True)
            return
        result = await self.cog.visibility(interaction, self.channel_id, field, not getattr(room, field))
        if not result.startswith("Готово"):
            await interaction.followup.send(embed=notice(result, kind="warning"), ephemeral=True)
            return
        room = await store.by_channel(self.cog.bot.database, interaction.guild_id, self.channel_id)
        channel = interaction.guild.get_channel(self.channel_id)
        await interaction.edit_original_response(embed=describe_room(room, channel), view=RoomControls(self.cog, self.channel_id))

    @discord.ui.button(label="Управление входом", emoji="🔒", style=discord.ButtonStyle.primary, row=0)
    async def lock(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._toggle(interaction, "locked")

    @discord.ui.button(label="Видимость", emoji="👁️", style=discord.ButtonStyle.secondary, row=0)
    async def hide(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._toggle(interaction, "hidden")

    @discord.ui.button(label="Название", emoji="✏️", style=discord.ButtonStyle.secondary, row=1)
    async def rename(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(RenameRoomModal(self.cog, self.channel_id))

    @discord.ui.button(label="Лимит", emoji="👥", style=discord.ButtonStyle.secondary, row=1)
    async def limit(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(LimitRoomModal(self.cog, self.channel_id))

    @discord.ui.button(label="Удалить комнату", emoji="🗑️", style=discord.ButtonStyle.danger, row=2)
    async def close(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_message(embed=notice("Удаление комнаты необратимо. В комнате не должно быть "
            "других участников.", kind="warning"), view=CloseRoomConfirm(self.cog, self.channel_id), ephemeral=True)

    @discord.ui.button(label="Пригласить", style=discord.ButtonStyle.success, row=3)
    async def invite(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_message(
            "Выберите участника. Бот выдаст разрешение и отправит приглашение личным сообщением.",
            view=RoomInvitePicker(self.cog, self.channel_id), ephemeral=True)

    @discord.ui.button(label="Обновить", emoji="🔄", style=discord.ButtonStyle.secondary, row=2)
    async def refresh(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.defer()
        room = await store.by_channel(self.cog.bot.database, interaction.guild_id, self.channel_id)
        if room is None:
            await interaction.edit_original_response(embed=notice("Комната удалена.", kind="warning"), view=None)
            return
        await interaction.edit_original_response(
            embed=describe_room(room, interaction.guild.get_channel(self.channel_id)), view=self)


class RoomInvitePicker(discord.ui.View):
    def __init__(self, cog: "Voice", channel_id: int):
        super().__init__(timeout=120)
        picker = discord.ui.UserSelect(placeholder="Кого пригласить?", min_values=1, max_values=1)

        async def choose(interaction: discord.Interaction) -> None:
            if interaction.guild_id != cog.bot.config.guild_id:
                await interaction.response.send_message("Недоступный сервер.", ephemeral=True)
                return
            member = interaction.guild.get_member(picker.values[0].id)
            if member is None:
                try:
                    member = await interaction.guild.fetch_member(picker.values[0].id)
                except discord.HTTPException:
                    await interaction.response.send_message("Участник не найден.", ephemeral=True)
                    return
            await interaction.response.defer(ephemeral=True, thinking=True)
            result = await cog.access_room(interaction, channel_id, member, "allow")
            if result.startswith("Готово"):
                channel = interaction.guild.get_channel(channel_id)
                if isinstance(channel, discord.VoiceChannel):
                    try:
                        await member.send(
                            f"NewSqad: вас пригласили в голосовую комнату {channel.mention}.",
                            allowed_mentions=discord.AllowedMentions.none())
                    except discord.HTTPException:
                        result += " Личные сообщения участника закрыты — сообщите ему вручную."
            await interaction.followup.send(result, ephemeral=True)
        picker.callback = choose
        self.add_item(picker)


