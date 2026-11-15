"""Настоящая интерактивная /botsettings с записью ролей и каналов в БД."""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from app.config import VERSION
from app.core.catalog import MODULES
from app.core.permissions import can_manage_settings

ROLE_SLOTS = (
    ("member_role_id", "Роль участника"),
    ("candidate_role_id", "Роль кандидата"),
    ("female_role_id", "Женская роль"),
    ("admin_role_id", "Роль администратора"),
    ("moderator_role_id", "Роль модератора"),
    ("reviewer_role_id", "Роль рассмотрения заявок"),
    ("support_role_id", "Роль поддержки"),
)
CHANNEL_SLOTS = (
    ("application_channel_id", "Канал подачи заявок"),
    ("review_channel_id", "Канал рассмотрения заявок"),
    ("membership_audit_channel_id", "Журнал заявок"),
    ("audit_channel_id", "Основной журнал событий"),
    ("welcome_channel_id", "Приветственный канал"),
)


def embed_card(title: str, description: str) -> discord.Embed:
    return discord.Embed(title=title, description=description, color=discord.Color.from_rgb(38, 50, 73))


class PrivateSettingsView(discord.ui.View):
    def __init__(self, client: "NewSqadBot", owner_id: int):
        super().__init__(timeout=300)
        self.client = client
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id or not can_manage_settings(interaction, self.client.config.owner_ids):
            await interaction.response.send_message("Эта панель доступна только администратору, который её открыл.", ephemeral=True)
            return False
        return True


class HomeView(PrivateSettingsView):
    @discord.ui.button(label="Состояние", emoji="📊", style=discord.ButtonStyle.secondary, row=0)
    async def status(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        db_ok = await self.client.database.ping()
        embed = embed_card("NewSquad · Состояние", f"Версия: **{VERSION}**\nСервер: `{self.client.config.guild_id}`")
        embed.add_field(name="Подключение", value="Работает" if self.client.is_ready() else "Подключается", inline=True)
        embed.add_field(name="База данных", value="Доступна" if db_ok else "Ошибка", inline=True)
        embed.add_field(name="Активные модули", value=str(sum(v == "active" for v in self.client.module_states.values())), inline=True)
        await interaction.response.edit_message(embed=embed, view=BackView(self.client, self.owner_id))

    @discord.ui.button(label="Модули", emoji="🧩", style=discord.ButtonStyle.secondary, row=0)
    async def modules(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        labels = {"planned": "запланирован", "active": "активен", "disabled": "отключён", "error": "ошибка загрузки"}
        rows = [f"**{m.title}** — {labels.get(self.client.module_states.get(m.key, 'planned'), 'неизвестно')}" for m in MODULES]
        embed = embed_card("NewSquad · Модули", "\n".join(rows))
        embed.set_footer(text="Модуль становится доступен только после реализации в отдельном файле.")
        await interaction.response.edit_message(embed=embed, view=BackView(self.client, self.owner_id))

    @discord.ui.button(label="Роли", emoji="🎭", style=discord.ButtonStyle.primary, row=1)
    async def roles(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            embed=embed_card("NewSquad · Роли", "Выберите назначение роли, затем саму Discord-роль.\nРоль участника пока не выдаётся автоматически: Membership появится в v0.2.0."),
            view=SlotMenuView(self.client, self.owner_id, "role"),
        )

    @discord.ui.button(label="Каналы", emoji="📁", style=discord.ButtonStyle.primary, row=1)
    async def channels(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            embed=embed_card("NewSquad · Каналы", "Выберите назначение канала, затем текстовый канал Discord."),
            view=SlotMenuView(self.client, self.owner_id, "channel"),
        )


class BackView(PrivateSettingsView):
    @discord.ui.button(label="Главная", emoji="↩️", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(embed=home_embed(), view=HomeView(self.client, self.owner_id))


def home_embed() -> discord.Embed:
    return embed_card(
        "NewSquad · Настройки бота",
        "Управление ядром NewSqadBot.\n\n"
        "**Состояние** — подключение и база данных.\n"
        "**Модули** — фактическая готовность функций.\n"
        "**Роли** — назначение служебных ролей.\n"
        "**Каналы** — настройка рабочих каналов.\n\n"
        "Параметры функциональных модулей появятся по мере их реализации.",
    )


class SlotMenu(discord.ui.Select):
    def __init__(self, client: "NewSqadBot", owner_id: int, slot_type: str):
        self.slot_type = slot_type
        self.client = client
        self.owner_id = owner_id
        slots = ROLE_SLOTS if slot_type == "role" else CHANNEL_SLOTS
        super().__init__(placeholder="Выберите настройку", min_values=1, max_values=1,
                         options=[discord.SelectOption(label=title, value=key) for key, title in slots])

    async def callback(self, interaction: discord.Interaction) -> None:
        slot = self.values[0]
        slots = dict(ROLE_SLOTS if self.slot_type == "role" else CHANNEL_SLOTS)
        await interaction.response.edit_message(
            embed=embed_card(f"Настройка · {slots[slot]}", "Выберите значение в выпадающем списке ниже. Изменение будет сохранено в базе данных."),
            view=ValuePickerView(self.client, self.owner_id, self.slot_type, slot),
        )


class SlotMenuView(PrivateSettingsView):
    def __init__(self, client: "NewSqadBot", owner_id: int, slot_type: str):
        super().__init__(client, owner_id)
        self.add_item(SlotMenu(client, owner_id, slot_type))

    @discord.ui.button(label="Главная", emoji="↩️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(embed=home_embed(), view=HomeView(self.client, self.owner_id))


class RolePicker(discord.ui.RoleSelect):
    def __init__(self, client: "NewSqadBot", owner_id: int, slot: str):
        super().__init__(placeholder="Выберите роль", min_values=1, max_values=1)
        self.client, self.owner_id, self.slot = client, owner_id, slot

    async def callback(self, interaction: discord.Interaction) -> None:
        role = self.values[0]
        if interaction.guild is None or role.guild.id != interaction.guild.id:
            await interaction.response.send_message("Роль не принадлежит серверу NewSquad.", ephemeral=True)
            return
        await self.client.database.set_setting(self.client.config.guild_id, self.slot, role.id, actor_id=interaction.user.id)
        await interaction.response.edit_message(
            embed=embed_card("Настройка сохранена", f"**{dict(ROLE_SLOTS)[self.slot]}:** {role.mention}\nID: `{role.id}`"),
            view=BackView(self.client, self.owner_id),
        )


class ChannelPicker(discord.ui.ChannelSelect):
    def __init__(self, client: "NewSqadBot", owner_id: int, slot: str):
        super().__init__(placeholder="Выберите канал", min_values=1, max_values=1,
                         channel_types=[discord.ChannelType.text])
        self.client, self.owner_id, self.slot = client, owner_id, slot

    async def callback(self, interaction: discord.Interaction) -> None:
        channel = self.values[0]
        channel_guild_id = getattr(channel, "guild_id", None)
        if channel_guild_id is None:
            channel_guild_id = getattr(getattr(channel, "guild", None), "id", None)
        if interaction.guild is None or channel_guild_id != interaction.guild.id:
            await interaction.response.send_message("Выберите текстовый канал NewSquad.", ephemeral=True)
            return
        await self.client.database.set_setting(self.client.config.guild_id, self.slot, channel.id, actor_id=interaction.user.id)
        await interaction.response.edit_message(
            embed=embed_card("Настройка сохранена", f"**{dict(CHANNEL_SLOTS)[self.slot]}:** {channel.mention}\nID: `{channel.id}`"),
            view=BackView(self.client, self.owner_id),
        )


class ValuePickerView(PrivateSettingsView):
    def __init__(self, client: "NewSqadBot", owner_id: int, slot_type: str, slot: str):
        super().__init__(client, owner_id)
        self.add_item(RolePicker(client, owner_id, slot) if slot_type == "role" else ChannelPicker(client, owner_id, slot))

    @discord.ui.button(label="Назад", emoji="↩️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        slot_type = "role" if any(key == getattr(self.children[0], "slot", None) for key, _ in ROLE_SLOTS) else "channel"
        title = "Роли" if slot_type == "role" else "Каналы"
        await interaction.response.edit_message(
            embed=embed_card(f"NewSquad · {title}", "Выберите назначение настройки."),
            view=SlotMenuView(self.client, self.owner_id, slot_type),
        )


class CoreCommands(commands.Cog):
    bot_tools = app_commands.Group(name="bot", description="Состояние и диагностика NewSqadBot")

    def __init__(self, client: "NewSqadBot"):
        self.client = client

    async def _authorized(self, interaction: discord.Interaction) -> bool:
        if can_manage_settings(interaction, self.client.config.owner_ids):
            return True
        await interaction.response.send_message("Управлять ботом могут только администраторы NewSquad.", ephemeral=True)
        return False

    @app_commands.command(name="botsettings", description="Открыть центр управления NewSqadBot")
    async def botsettings(self, interaction: discord.Interaction) -> None:
        if not await self._authorized(interaction):
            return
        await interaction.response.send_message(embed=home_embed(), view=HomeView(self.client, interaction.user.id), ephemeral=True)

    @bot_tools.command(name="status", description="Проверить работу ядра бота")
    async def bot_status(self, interaction: discord.Interaction) -> None:
        if not await self._authorized(interaction):
            return
        ok = await self.client.database.ping()
        await interaction.response.send_message(
            embed=embed_card("Состояние NewSqadBot", f"Версия: `{VERSION}`\nБаза данных: {'доступна' if ok else 'недоступна'}\nАктивных модулей: {sum(s == 'active' for s in self.client.module_states.values())}"),
            ephemeral=True,
        )

    @bot_tools.command(name="modules", description="Показать фактический статус модулей")
    async def bot_modules(self, interaction: discord.Interaction) -> None:
        if not await self._authorized(interaction):
            return
        states = {"planned": "в планах", "active": "работает", "disabled": "отключён", "error": "ошибка"}
        lines = [f"• {m.title}: {states.get(self.client.module_states.get(m.key, 'planned'), 'неизвестно')}" for m in MODULES]
        await interaction.response.send_message(embed=embed_card("Модули NewSqadBot", "\n".join(lines)), ephemeral=True)
