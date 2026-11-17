"""Настоящая интерактивная /botsettings с записью ролей и каналов в БД."""
from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select

from app.config import VERSION
from app.core.catalog import MODULES
from app.core.permissions import can_manage_settings
from app.models import RolePanel
from app.ui.mentions import channel_mention, role_mention
from app.ui.theme import LoggedView, card, notice, panel_card

if TYPE_CHECKING:
    from app.bot import NewSqadBot

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
    ("moderation_audit_channel_id", "Журнал модерации"),
    ("welcome_channel_id", "Приветственный канал"),
    ("farewell_channel_id", "Канал прощаний"),
    ("rules_channel_id", "Канал правил"),
    ("ticket_panel_channel_id", "Канал панели тикетов"),
    ("ticket_category_channel_id", "Категория приватных тикетов"),
    ("ticket_log_channel_id", "Журнал тикетов"),
    ("voice_lobby_channel_id", "Голосовой канал создания"),
    ("voice_category_channel_id", "Категория голосовых комнат"),
    ("event_channel_id", "Канал мероприятий"),
    ("giveaway_channel_id", "Канал розыгрышей"),
    ("suggest_channel_id", "Канал предложений"),
)


def embed_card(title: str, description: str) -> discord.Embed:
    return card(title, description)


class PrivateSettingsView(LoggedView):
    def __init__(self, client: "NewSqadBot", owner_id: int):
        super().__init__(timeout=300)
        self.client = client
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not await super().interaction_check(interaction):
            return False
        if interaction.user.id != self.owner_id or not can_manage_settings(interaction, self.client.config.owner_ids):
            await interaction.response.send_message(embed=notice("Эта панель доступна только администратору, который её открыл.", kind="warning"), ephemeral=True)
            return False
        return True


class HubSelect(discord.ui.Select):
    """Разделы по назначению вместо 19 кнопок на одном экране."""
    def __init__(self, home: "HomeView", labels: tuple[str, ...],
                 placeholder: str, row: int):
        self.routes = {label: home._action_buttons[label] for label in labels}
        super().__init__(
            placeholder=placeholder, min_values=1, max_values=1, row=row,
            options=[discord.SelectOption(
                label=label, value=label,
                description="Открыть раздел настроек",
            ) for label in labels],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        button = self.routes[self.values[0]]
        await button.callback(interaction)


class HomeView(PrivateSettingsView):
    def __init__(self, client: "NewSqadBot", owner_id: int):
        super().__init__(client, owner_id)
        self._action_buttons = {
            button.label: button for button in self.children
            if isinstance(button, discord.ui.Button)
        }
        self.clear_items()
        featured = (
            ("Состояние", 0), ("Роли", 0), ("Каналы", 0),
            ("Объявления", 1), ("Диагностика", 1), ("Все настройки", 1),
        )
        for label, row in featured:
            button = self._action_buttons[label]
            button.row = row
            self.add_item(button)
        self.add_item(HubSelect(
            self,
            ("Приветствия", "Роли участников", "Тикеты", "Профили"),
            "Участники и поддержка", 2,
        ))
        self.add_item(HubSelect(
            self,
            ("Модули", "Мероприятия", "Предложения", "Статистика",
             "Голосовые комнаты", "Уровни и XP", "Экономика",
             "Задания", "Достижения"),
            "Модули и активность сообщества", 3,
        ))


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
            embed=embed_card("NewSquad · Роли", "Выберите назначение роли, затем саму Discord-роль.\nВыберите роли, используемые действующей системой Membership."),
            view=SlotMenuView(self.client, self.owner_id, "role"),
        )

    @discord.ui.button(label="Каналы", emoji="📁", style=discord.ButtonStyle.primary, row=1)
    async def channels(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            embed=embed_card("NewSquad · Каналы", "Выберите назначение канала или категории, затем объект Discord."),
            view=SlotMenuView(self.client, self.owner_id, "channel"),
        )

    @discord.ui.button(label="Приветствия", emoji="👋", style=discord.ButtonStyle.secondary, row=2)
    async def welcome(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await show_welcome_settings(interaction, self.client, self.owner_id)

    @discord.ui.button(label="Роли участников", emoji="🎮", style=discord.ButtonStyle.secondary, row=2)
    async def role_panels(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await show_role_panels(interaction, self.client, self.owner_id)

    @discord.ui.button(label="Тикеты", emoji="🎫", style=discord.ButtonStyle.secondary, row=3)
    async def tickets(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await show_tickets_settings(interaction, self.client, self.owner_id)

    @discord.ui.button(label="Мероприятия", emoji="🎉", style=discord.ButtonStyle.secondary, row=2)
    async def events(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await show_events_settings(interaction, self.client, self.owner_id)

    @discord.ui.button(label="Предложения", emoji="💡", style=discord.ButtonStyle.secondary, row=2)
    async def suggestions(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await show_suggestions_settings(interaction, self.client, self.owner_id)

    @discord.ui.button(label="Статистика", emoji="📈", style=discord.ButtonStyle.secondary, row=2)
    async def statistics(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await show_statistics_settings(interaction, self.client, self.owner_id)

    @discord.ui.button(label="Голосовые комнаты", emoji="🎙️", style=discord.ButtonStyle.secondary, row=3)
    async def voice_rooms(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await show_voice_settings(interaction, self.client, self.owner_id)

    @discord.ui.button(label="Уровни и XP", emoji="🏆", style=discord.ButtonStyle.secondary, row=3)
    async def leveling(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await show_leveling_settings(interaction, self.client, self.owner_id)

    @discord.ui.button(label="Профили", emoji="👤", style=discord.ButtonStyle.secondary, row=3)
    async def profiles(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            embed=embed_card("NewSquad  /  Профили",
                "**Публичные анкеты участников**\n"
                "`/profile view` — открыть профиль.\n"
                "`/profile edit` — изменить описание, интересы или игры.\n"
                "`/profile status` — отметить поиск компании.\n"
                "`/profile privacy` — закрыть профиль.\n\n"
                "Профиль не раскрывает имя, возраст или ответы из анкеты Membership. "
                "Доступ определяется отсутствием роли кандидата."),
            view=BackView(self.client, self.owner_id),
        )

    @discord.ui.button(label="Экономика", emoji="🪙", style=discord.ButtonStyle.secondary, row=4)
    async def economy(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await show_economy_settings(interaction, self.client, self.owner_id)

    @discord.ui.button(label="Задания", emoji="🗓️", style=discord.ButtonStyle.secondary, row=4)
    async def quests(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await show_engagement_settings(interaction, self.client, self.owner_id, "quests")

    @discord.ui.button(label="Достижения", emoji="🏅", style=discord.ButtonStyle.secondary, row=4)
    async def achievements(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await show_engagement_settings(interaction, self.client, self.owner_id, "achievements")

    @discord.ui.button(label="Объявления", emoji="📢", style=discord.ButtonStyle.secondary, row=1)
    async def announcements(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await show_announcement_settings(interaction, self.client, self.owner_id)

    @discord.ui.button(label="Диагностика", emoji="🩺", style=discord.ButtonStyle.secondary, row=0)
    async def diagnose(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        from app.services.diagnostics import collect, diagnostic_embed

        await interaction.response.defer()
        result = await collect(self.client)
        await interaction.edit_original_response(
            embed=diagnostic_embed(result), view=BackView(self.client, self.owner_id),
        )

    @discord.ui.button(label="Все настройки", emoji="🗂️", style=discord.ButtonStyle.primary, row=4)
    async def settings_overview(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        from app.panels.settings_overview import SettingsOverviewView, overview_embed
        await interaction.response.defer()
        embed, pages = await overview_embed(self.client, "summary")
        view = SettingsOverviewView(self.client, self.owner_id)
        view.max_pages = pages
        view._buttons()
        await interaction.edit_original_response(embed=embed, view=view)


class AnnouncementSettingsView(PrivateSettingsView):
    @discord.ui.button(label="Черновики", style=discord.ButtonStyle.primary)
    async def drafts(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        from app.services import announcements as store

        items = await store.list_items(
            self.client.database, self.client.config.guild_id, statuses=("draft", "failed"),
        )
        text = "\n".join(
            f"**#{item.id}** {discord.utils.escape_mentions(item.title[:80])}"
            for item in items
        ) or "Нет сохранённых черновиков."
        await interaction.response.edit_message(
            embed=embed_card("NewSquad / Черновики", text),
            view=BackView(self.client, self.owner_id),
        )

    @discord.ui.button(label="Расписание", style=discord.ButtonStyle.secondary)
    async def scheduled(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        from app.services import announcements as store

        items = await store.list_items(
            self.client.database, self.client.config.guild_id, statuses=("scheduled",),
        )
        text = "\n".join(
            f"**#{item.id}** {discord.utils.escape_mentions(item.title[:60])}"
            f" · <t:{item.due_at}:F>" for item in items
        ) or "Нет запланированных публикаций."
        await interaction.response.edit_message(
            embed=embed_card("NewSquad / Расписание объявлений", text),
            view=BackView(self.client, self.owner_id),
        )

    @discord.ui.button(label="Главная", style=discord.ButtonStyle.secondary)
    async def home(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            embed=home_embed(), view=HomeView(self.client, self.owner_id),
        )


async def show_announcement_settings(
    interaction: discord.Interaction, client: "NewSqadBot", owner_id: int,
) -> None:
    from app.services import announcements as store

    gid = client.config.guild_id
    drafts = await store.list_items(client.database, gid, statuses=("draft", "failed"))
    planned = await store.list_items(client.database, gid, statuses=("scheduled",))
    templates = await store.templates(client.database, gid)
    body = (
        f"**Черновики:** {len(drafts)} (последние 15)\n"
        f"**Запланировано:** {len(planned)} (последние 15)\n"
        f"**Шаблонов:** {len(templates)}\n"
        f"**Время сервера:** {client.config.timezone}\n\n"
        "**Создать:** /ann\n"
        "**Управление:** /announcements drafts, scheduled, history\n"
        "**Шаблоны:** /announcements templates, preset\n"
        "**Отмена:** /announcements cancel\n\n"
        "После /ann сначала показывается предпросмотр. "
        "Отправка происходит только после подтверждения.",
    )
    await interaction.response.edit_message(
        embed=embed_card("NewSquad / Центр объявлений", body),
        view=AnnouncementSettingsView(client, owner_id),
    )


class StatisticsSettingsView(PrivateSettingsView):
    @discord.ui.button(label="Учёт: включить / выключить", style=discord.ButtonStyle.primary)
    async def toggle(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        key = "statistics_enabled"
        gid = self.client.config.guild_id
        value = bool(await self.client.database.get_setting(gid, key, True))
        await self.client.database.set_setting(gid, key, not value, actor_id=interaction.user.id)
        if not value:
            cog = self.client.get_cog("Statistics")
            if cog:
                await cog.init_tracking()
        await show_statistics_settings(interaction, self.client, self.owner_id)

    @discord.ui.button(label="Публичный рейтинг: переключить", style=discord.ButtonStyle.secondary)
    async def ranking(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        key = "statistics_public_ranking"
        gid = self.client.config.guild_id
        value = bool(await self.client.database.get_setting(gid, key, True))
        await self.client.database.set_setting(gid, key, not value, actor_id=interaction.user.id)
        await show_statistics_settings(interaction, self.client, self.owner_id)

    @discord.ui.button(label="Главная", emoji="↩️", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            embed=home_embed(), view=HomeView(self.client, self.owner_id))


async def show_statistics_settings(interaction: discord.Interaction,
                                   client: "NewSqadBot", owner_id: int) -> None:
    from app.services import statistics as store

    gid = client.config.guild_id
    enabled = await client.database.get_setting(gid, "statistics_enabled", True)
    messages = await client.database.get_setting(gid, "statistics_messages_enabled", True)
    voice = await client.database.get_setting(gid, "statistics_voice_enabled", True)
    ranking = await client.database.get_setting(gid, "statistics_public_ranking", True)
    timezone = await client.database.get_setting(gid, "statistics_timezone", client.config.timezone)
    started = await client.database.get_setting(gid, "statistics_started_at")
    panels = await store.panels(client.database, gid)
    description = (
        f"**Сбор:** {'включён' if enabled else 'выключен'}\n"
        f"**Сообщения:** {'да' if messages else 'нет'}\n"
        f"**Голос:** {'да' if voice else 'нет'}\n"
        f"**Публичные рейтинги:** {'да' if ranking else 'нет'}\n"
        f"**Начало учёта:** {f'<t:{started}:f>' if started else 'не начат'}\n"
        f"**Часовой пояс:** {timezone}\n"
        f"**Панелей:** {len(panels)}\n\n"
        "Команды: /stats server, /stats user, /stats top, /stats activity.\n"
        "Настроить: /stats configure; опубликовать: /stats panel."
    )
    await interaction.response.edit_message(
        embed=embed_card("NewSquad / Статистика", description),
        view=StatisticsSettingsView(client, owner_id))


class SuggestionsSettingsView(PrivateSettingsView):
    @discord.ui.button(label="Включить / выключить", style=discord.ButtonStyle.primary)
    async def toggle(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        gid = self.client.config.guild_id
        enabled = bool(await self.client.database.get_setting(gid, "suggestions_enabled", True))
        await self.client.database.set_setting(
            gid, "suggestions_enabled", not enabled, actor_id=interaction.user.id)
        await show_suggestions_settings(interaction, self.client, self.owner_id)

    @discord.ui.button(label="Канал предложений", style=discord.ButtonStyle.secondary)
    async def channel(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            embed=embed_card("NewSquad / Канал идей",
                             "Выберите текстовый канал для публикации предложений."),
            view=ValuePickerView(self.client, self.owner_id, "channel", "suggest_channel_id"),
        )

    @discord.ui.button(label="Главная", emoji="↩️", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            embed=home_embed(), view=HomeView(self.client, self.owner_id))


async def show_suggestions_settings(
    interaction: discord.Interaction, client: "NewSqadBot", owner_id: int,
) -> None:
    from app.services import suggestions as store

    gid = client.config.guild_id
    enabled = await client.database.get_setting(gid, "suggestions_enabled", True)
    channel_id = await client.database.get_setting(gid, "suggest_channel_id")
    minimum = await client.database.get_setting(gid, "suggest_min_level", 1)
    cooldown = await client.database.get_setting(gid, "suggest_cooldown_seconds", 1800)
    missing, panels = await store.missing_count(client.database, gid)
    info = (
        f"**Приём идей:** {'включён' if enabled else 'отключён'}\n"
        f"**Канал:** {f'<#{channel_id}>' if channel_id else 'не выбран'}\n"
        f"**Минимальный уровень:** {minimum}\n"
        f"**Задержка между идеями:** {cooldown // 60} минут\n"
        f"**Удалённые карточки:** {missing} · **панели подачи:** {panels}\n\n"
        "Настройки: /suggest configure, /suggest settings.\n"
        "Панель создания: /suggest panel; восстановление: /suggest restore."
    )
    await interaction.response.edit_message(
        embed=embed_card("NewSquad / Предложения", info),
        view=SuggestionsSettingsView(client, owner_id))


class EventsSettingsView(PrivateSettingsView):
    @discord.ui.button(label="Мероприятия: включить / выключить", style=discord.ButtonStyle.primary)
    async def toggle_events(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        key = "events_enabled"
        value = bool(await self.client.database.get_setting(self.client.config.guild_id, key, True))
        await self.client.database.set_setting(self.client.config.guild_id, key, not value,
                                               actor_id=interaction.user.id)
        await show_events_settings(interaction, self.client, self.owner_id)

    @discord.ui.button(label="Розыгрыши: включить / выключить", style=discord.ButtonStyle.secondary)
    async def toggle_giveaways(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        key = "giveaways_enabled"
        value = bool(await self.client.database.get_setting(self.client.config.guild_id, key, True))
        await self.client.database.set_setting(self.client.config.guild_id, key, not value,
                                               actor_id=interaction.user.id)
        await show_events_settings(interaction, self.client, self.owner_id)

    @discord.ui.button(label="Главная", emoji="↩️", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(embed=home_embed(), view=HomeView(self.client, self.owner_id))


async def show_events_settings(interaction: discord.Interaction, client: "NewSqadBot",
                               owner_id: int) -> None:
    gid = client.config.guild_id
    active = await client.database.get_setting(gid, "events_enabled", True)
    giveaways = await client.database.get_setting(gid, "giveaways_enabled", True)
    channel = await client.database.get_setting(gid, "event_channel_id")
    prize_channel = await client.database.get_setting(gid, "giveaway_channel_id")
    await interaction.response.edit_message(embed=embed_card("NewSquad / Мероприятия",
        f"**Мероприятия:** {'включены' if active else 'выключены'}\n"
        f"**Розыгрыши:** {'включены' if giveaways else 'выключены'}\n"
        f"**Канал мероприятий:** {f'<#{channel}>' if channel else 'не выбран'}\n"
        f"**Канал розыгрышей:** {f'<#{prize_channel}>' if prize_channel else 'не выбран'}\n"
        f"**Часовой пояс:** {client.config.timezone}\n\n"
        "Создать мероприятие: /event create. Подтвердить участие: /event confirm.\n"
        "Создать розыгрыш: /giveaway create; история: /giveaway history.\n"
        "Каналы можно назначить в разделе «Каналы» и указывать при создании."),
        view=EventsSettingsView(client, owner_id))


class VoiceSettingsView(PrivateSettingsView):
    @discord.ui.button(label="Включить / выключить", style=discord.ButtonStyle.primary, row=0)
    async def toggle(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        key = "voice_enabled"
        current = bool(await self.client.database.get_setting(self.client.config.guild_id, key, True))
        await self.client.database.set_setting(self.client.config.guild_id, key, not current,
                                               actor_id=interaction.user.id)
        await show_voice_settings(interaction, self.client, self.owner_id)

    @discord.ui.button(label="Канал создания", emoji="🎤", style=discord.ButtonStyle.secondary, row=0)
    async def lobby(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            embed=embed_card("NewSquad  /  Канал создания", "Выберите голосовой канал, вход в который "
                "будет создавать временную комнату для одобренного участника."),
            view=ValuePickerView(self.client, self.owner_id, "channel", "voice_lobby_channel_id"),
        )

    @discord.ui.button(label="Категория комнат", emoji="📂", style=discord.ButtonStyle.secondary, row=0)
    async def category(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            embed=embed_card("NewSquad  /  Категория комнат", "Выберите категорию Discord, "
                "в которой будут появляться новые голосовые каналы."),
            view=ValuePickerView(self.client, self.owner_id, "channel", "voice_category_channel_id"),
        )

    @discord.ui.button(label="Главная", emoji="↩️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(embed=home_embed(), view=HomeView(self.client, self.owner_id))


async def show_voice_settings(interaction: discord.Interaction, client: "NewSqadBot", owner_id: int) -> None:
    from app.services import voice as store

    gid = client.config.guild_id
    enabled = await client.database.get_setting(gid, "voice_enabled", True)
    lobby_id = await client.database.get_setting(gid, "voice_lobby_channel_id")
    category_id = await client.database.get_setting(gid, "voice_category_channel_id")
    max_rooms = await client.database.get_setting(gid, "voice_max_rooms", 30)
    limit = await client.database.get_setting(gid, "voice_default_limit", 0)
    active = len(await store.all_rooms(client.database, gid))
    text = (
        f"**Состояние:** {'включены' if enabled else 'отключены'}\n"
        f"**Канал создания:** {f'<#{lobby_id}>' if lobby_id else 'не выбран'}\n"
        f"**Категория голосовых комнат:** {category_id or 'не выбрана'}\n"
        f"**Активных комнат:** {active} / {max_rooms}\n"
        f"**Лимит по умолчанию:** {limit or 'без ограничений'}\n\n"
        "Быстрая настройка: `/voice setup`, `/voice configure`.\n"
        "Панель владельца комнаты: `/voice panel`.\n"
        "Требуется роль участника и разрешения Manage Channels, Move Members у бота."
    )
    await interaction.response.edit_message(embed=embed_card("NewSquad  /  Голосовые комнаты", text),
                                            view=VoiceSettingsView(client, owner_id))


class EngagementSettingsView(PrivateSettingsView):
    def __init__(self, client: "NewSqadBot", owner_id: int, module: str):
        super().__init__(client, owner_id)
        self.module = module

    @discord.ui.button(label="Включить / выключить", style=discord.ButtonStyle.primary, row=0)
    async def toggle(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        key = f"{self.module}_enabled"
        state = await self.client.database.get_setting(self.client.config.guild_id, key, True)
        await self.client.database.set_setting(self.client.config.guild_id, key, not state,
                                               actor_id=interaction.user.id)
        await show_engagement_settings(interaction, self.client, self.owner_id, self.module)

    @discord.ui.button(label="Главная", emoji="↩️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(embed=home_embed(), view=HomeView(self.client, self.owner_id))


async def show_engagement_settings(interaction: discord.Interaction, client: "NewSqadBot",
                                   owner_id: int, module: str) -> None:
    if module not in {"quests", "achievements"}:
        raise ValueError("Неизвестный модуль")
    enabled = await client.database.get_setting(client.config.guild_id, f"{module}_enabled", True)
    if module == "quests":
        daily = await client.database.get_setting(client.config.guild_id, "quests_daily_enabled", True)
        weekly = await client.database.get_setting(client.config.guild_id, "quests_weekly_enabled", True)
        text = (f"**Задания:** {'включены' if enabled else 'отключены'}\n"
                f"**Ежедневные:** {'включены' if daily else 'отключены'}\n"
                f"**Еженедельные:** {'включены' if weekly else 'отключены'}\n"
                f"**Часовой пояс:** {client.config.timezone}\n\n"
                "Правила: `/quests settings`, переключатели: `/quests switch`.\n"
                "Получение наград: `/quests daily`, `/quests weekly`.")
        title = "Задания"
    else:
        text = (f"**Достижения:** {'включены' if enabled else 'отключены'}\n"
                "**Сохранение:** постоянное, без сезонного сброса.\n"
                "**Награды:** XP и NS Coins, только один раз.\n\n"
                "Просмотр: `/achievements list`; настройка: `/achievements switch`.")
        title = "Достижения"
    await interaction.response.edit_message(embed=embed_card(f"NewSquad  /  {title}", text),
                                            view=EngagementSettingsView(client, owner_id, module))


class EconomySettingsView(PrivateSettingsView):
    async def _toggle(self, interaction: discord.Interaction, key: str) -> None:
        current = bool(await self.client.database.get_setting(self.client.config.guild_id, key, True))
        await self.client.database.set_setting(self.client.config.guild_id, key, not current,
                                               actor_id=interaction.user.id)
        await show_economy_settings(interaction, self.client, self.owner_id)

    @discord.ui.button(label="Включить экономику", style=discord.ButtonStyle.primary, row=0)
    async def enabled(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._toggle(interaction, "economy_enabled")

    @discord.ui.button(label="Разрешить переводы", style=discord.ButtonStyle.secondary, row=0)
    async def transfers(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._toggle(interaction, "economy_transfers_enabled")

    @discord.ui.button(label="Награды за XP", style=discord.ButtonStyle.secondary, row=0)
    async def activity(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._toggle(interaction, "economy_activity_enabled")

    @discord.ui.button(label="Главная", emoji="↩️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(embed=home_embed(), view=HomeView(self.client, self.owner_id))


async def show_economy_settings(interaction: discord.Interaction, client: "NewSqadBot", owner_id: int) -> None:
    settings = (("economy_enabled", "Экономика включена", True),
                ("economy_transfers_enabled", "Переводы", True),
                ("economy_activity_enabled", "Награды за активность", True),
                ("economy_daily_reward", "Ежедневно", 100),
                ("economy_message_reward", "За текстовый XP", 2),
                ("economy_voice_reward", "За голосовой XP", 5),
                ("economy_level_reward", "За уровень", 25))
    lines = []
    for key, label, default in settings:
        value = await client.database.get_setting(client.config.guild_id, key, default)
        lines.append(f"**{label}:** {value if not isinstance(default, bool) else ('включено' if value else 'выключено')}")
    lines += ["", "Товары: `/shop list` / `/shop create` / `/shop disable`.",
              "Награды: `/economy rates`. Все операции доступны в `/economy history`."]
    await interaction.response.edit_message(embed=embed_card("NewSquad  /  Экономика", "\n".join(lines)),
                                            view=EconomySettingsView(client, owner_id))


class ProgressSettingsView(PrivateSettingsView):
    async def _toggle(self, interaction: discord.Interaction, setting: str) -> None:
        key = f"leveling_{setting}_enabled" if setting else "leveling_enabled"
        current = await self.client.database.get_setting(self.client.config.guild_id, key, True)
        await self.client.database.set_setting(self.client.config.guild_id, key, not current,
                                               actor_id=interaction.user.id)
        await show_leveling_settings(interaction, self.client, self.owner_id)

    @discord.ui.button(label="Общий прогресс", style=discord.ButtonStyle.primary, row=0)
    async def total(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._toggle(interaction, "")

    @discord.ui.button(label="Текст", style=discord.ButtonStyle.secondary, row=0)
    async def text(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._toggle(interaction, "text")

    @discord.ui.button(label="Голосовые", style=discord.ButtonStyle.secondary, row=0)
    async def voice(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._toggle(interaction, "voice")

    @discord.ui.button(label="Главная", emoji="↩️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(embed=home_embed(), view=HomeView(self.client, self.owner_id))


async def show_leveling_settings(interaction: discord.Interaction, client: "NewSqadBot", owner_id: int) -> None:
    guild_id = client.config.guild_id
    fields = {
        "leveling_enabled": ("Система уровней", True),
        "leveling_text_enabled": ("Текстовый XP", True),
        "leveling_voice_enabled": ("Голосовой XP", True),
        "leveling_message_xp": ("XP за сообщение", 20),
        "leveling_voice_xp": ("XP за 10 минут", 30),
        "leveling_cooldown_seconds": ("Задержка между сообщениями, сек", 60),
        "leveling_daily_cap": ("Дневной лимит XP", 1200),
    }
    lines = []
    for key, (title, default) in fields.items():
        value = await client.database.get_setting(guild_id, key, default)
        if isinstance(default, bool):
            value = "включено" if value else "выключено"
        lines.append(f"**{title}:** {value}")
    lines += ["", "Для настройки значений: `/levels rates` и `/levels settings`.",
              "Исключение каналов: `/levels exclude` / `/levels include`.",
              "Текстовый XP требует `ENABLE_MESSAGE_CONTENT_INTENT=true` в `.env` и разрешения Discord."]
    await interaction.response.edit_message(
        embed=embed_card("NewSquad  /  Уровни и XP", "\n".join(lines)),
        view=ProgressSettingsView(client, owner_id),
    )


class TicketSettingsView(PrivateSettingsView):
    @discord.ui.button(label="Включить / выключить", style=discord.ButtonStyle.primary, row=0)
    async def toggle(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        enabled = await self.client.database.get_setting(self.client.config.guild_id, "tickets_enabled", True)
        await self.client.database.set_setting(self.client.config.guild_id, "tickets_enabled", not enabled,
                                              actor_id=interaction.user.id)
        await show_tickets_settings(interaction, self.client, self.owner_id)

    @discord.ui.button(label="Лимит 1", style=discord.ButtonStyle.secondary, row=0)
    async def limit_one(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.set_limit(interaction, 1)

    @discord.ui.button(label="Лимит 3", style=discord.ButtonStyle.secondary, row=0)
    async def limit_three(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.set_limit(interaction, 3)

    async def set_limit(self, interaction: discord.Interaction, value: int) -> None:
        await self.client.database.set_setting(self.client.config.guild_id, "tickets_max_open", value,
                                              actor_id=interaction.user.id)
        await show_tickets_settings(interaction, self.client, self.owner_id)

    @discord.ui.button(label="Категория каналов", emoji="📂", style=discord.ButtonStyle.secondary, row=1)
    async def location(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            embed=embed_card("NewSquad · Категория тикетов",
                             "Выберите **категорию Discord** (раздел в списке каналов), "
                             "в которой бот будет создавать приватные обращения. "
                             "Это не канал публикации панели и не категория обращения."),
            view=ValuePickerView(self.client, self.owner_id, "channel", "ticket_category_channel_id"),
        )

    @discord.ui.button(label="Главная", emoji="↩️", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(embed=home_embed(), view=HomeView(self.client, self.owner_id))


async def show_tickets_settings(interaction: discord.Interaction, client: "NewSqadBot", owner_id: int) -> None:
    guild_id = client.config.guild_id
    enabled = await client.database.get_setting(guild_id, "tickets_enabled", True)
    limit = await client.database.get_setting(guild_id, "tickets_max_open", 3)
    idle_days = await client.database.get_setting(guild_id, "tickets_inactivity_days", 0)
    room_id = await client.database.get_setting(guild_id, "ticket_category_channel_id")
    role_id = await client.database.get_setting(guild_id, "support_role_id")
    from app.services.tickets import categories
    items = await categories(client.database, guild_id)
    text = (f"**Приём обращений:** {'включён' if enabled else 'отключён'}\n"
            f"**Максимум одновременно:** {limit}\n"
            f"**Автозакрытие:** {idle_days} дней (0 — выключено)\n"
            f"**Категория приватных каналов:** {channel_mention(client.get_guild(guild_id), room_id)}\n"
            f"**Роль поддержки:** {role_mention(None, guild=client.get_guild(guild_id), role_id=role_id)}\n"
            "**После закрытия:** канал и история удаляются\n"
            "**Журнал и экспорт тикетов:** отключены\n"
            f"**Категории:** {len(items)}\n\n"
            "Опубликовать панель: `/tickets panel`\n"
            "Выбрать категорию каналов: кнопка выше или `/tickets location`\n"
            "Категории: `/tickets category create` и `/tickets category edit`\n"
            "Роль поддержки: главная панель → Роли.")
    await interaction.response.edit_message(embed=embed_card("NewSquad · Тикеты", text),
                                            view=TicketSettingsView(client, owner_id))


class BackView(PrivateSettingsView):
    @discord.ui.button(label="Главная", emoji="↩️", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(embed=home_embed(), view=HomeView(self.client, self.owner_id))


def home_embed() -> discord.Embed:
    return panel_card(
        "NewSquad  /  Центр управления",
        "Настройки сервера, права участников и инструменты управления.",
        ("Быстрый доступ", "Состояние · Роли · Каналы · Объявления · Диагностика"),
        ("Настройки сообщества", "Откройте один из разделов в двух выпадающих меню ниже."),
        ("Безопасность", "Доступ только администраторам. Изменения сохраняются в SQLite."),
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
            embed=embed_card("NewSquad / Роль сохранена", f"**{dict(ROLE_SLOTS)[self.slot]}**\n{role_mention(role)}\n\nИзменение сохранено в настройках сервера."),
            view=BackView(self.client, self.owner_id),
        )


class ChannelPicker(discord.ui.ChannelSelect):
    def __init__(self, client: "NewSqadBot", owner_id: int, slot: str):
        self.category_slot = slot in {"ticket_category_channel_id", "voice_category_channel_id"}
        self.voice_slot = slot == "voice_lobby_channel_id"
        channel_type = (discord.ChannelType.category if self.category_slot else
                        discord.ChannelType.voice if self.voice_slot else discord.ChannelType.text)
        super().__init__(
            placeholder=("Выберите категорию Discord" if self.category_slot else
                         "Выберите голосовой канал" if self.voice_slot else "Выберите текстовый канал"),
            min_values=1,
            max_values=1,
            channel_types=[channel_type],
        )
        self.client, self.owner_id, self.slot = client, owner_id, slot

    async def callback(self, interaction: discord.Interaction) -> None:
        selected = self.values[0]
        channel = interaction.guild.get_channel(selected.id) if interaction.guild else None
        expected = (discord.CategoryChannel if self.category_slot else
                    discord.VoiceChannel if self.voice_slot else discord.TextChannel)
        if not isinstance(channel, expected) or channel.guild.id != self.client.config.guild_id:
            await interaction.response.send_message(
                "Выберите категорию Discord на сервере NewSquad."
                if self.category_slot else "Выберите голосовой канал NewSquad."
                if self.voice_slot else "Выберите текстовый канал NewSquad.",
                ephemeral=True,
            )
            return
        await self.client.database.set_setting(
            self.client.config.guild_id, self.slot, channel.id, actor_id=interaction.user.id
        )
        display = channel_mention(interaction.guild, channel.id)
        await interaction.response.edit_message(
            embed=embed_card("Настройка сохранена",
                             f"**{dict(CHANNEL_SLOTS)[self.slot]}**\n{display}\n\nНастройка сохранена."),
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


async def show_welcome_settings(interaction: discord.Interaction, client: "NewSqadBot", owner_id: int) -> None:
    flags = (
        ("welcome_enabled", "Приветствие после одобрения"),
        ("welcome_farewell_enabled", "Сообщение о выходе"),
        ("welcome_candidate_dm", "ЛС при вступлении кандидата"),
    )
    lines = []
    for key, title in flags:
        enabled = bool(await client.database.get_setting(client.config.guild_id, key, key == "welcome_enabled"))
        lines.append(f"**{title}:** {'включено' if enabled else 'выключено'}")
    lines.append("\nШаблоны меняются через `/welcome template`, каналы — во вкладке **Каналы**.")
    await interaction.response.edit_message(
        embed=embed_card("NewSquad · Приветствия", "\n".join(lines)),
        view=WelcomeSettingsView(client, owner_id),
    )


class WelcomeSettingsView(PrivateSettingsView):
    async def _toggle(self, interaction: discord.Interaction, key: str, default: bool = False) -> None:
        current = bool(await self.client.database.get_setting(self.client.config.guild_id, key, default))
        await self.client.database.set_setting(self.client.config.guild_id, key, not current,
            actor_id=interaction.user.id)
        # One response only: show_welcome_settings edits this interaction's message.
        await show_welcome_settings(interaction, self.client, self.owner_id)

    @discord.ui.button(label="Приветствие", style=discord.ButtonStyle.primary, row=0)
    async def greeting(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._toggle(interaction, "welcome_enabled", True)

    @discord.ui.button(label="Прощание", style=discord.ButtonStyle.secondary, row=0)
    async def farewell(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._toggle(interaction, "welcome_farewell_enabled")

    @discord.ui.button(label="ЛС кандидатам", style=discord.ButtonStyle.secondary, row=0)
    async def candidate_dm(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._toggle(interaction, "welcome_candidate_dm")

    @discord.ui.button(label="Главная", emoji="↩️", style=discord.ButtonStyle.secondary, row=1)
    async def home(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(embed=home_embed(), view=HomeView(self.client, self.owner_id))


async def show_role_panels(interaction: discord.Interaction, client: "NewSqadBot", owner_id: int) -> None:
    async with client.database.sessions() as session:
        panels = (await session.scalars(select(RolePanel).where(
            RolePanel.guild_id == client.config.guild_id,
            RolePanel.enabled.is_(True)).order_by(RolePanel.id.desc()).limit(10))).all()
    lines = [f"**#{p.id}** · {discord.utils.escape_mentions(p.title)} — {'опубликована' if p.message_id else 'черновик'}" for p in panels]
    content = "\n".join(lines) or "Панелей пока нет."
    content += "\n\nСоздать панель: `/roles panel create`. Добавить роль: `/roles panel add`. Опубликовать: `/roles panel publish`."
    content += "\nАвтовыдача: `/roles autorole add`. Временные роли: `/roles temporary`."
    await interaction.response.edit_message(embed=embed_card("NewSquad · Роли участников", content),
        view=BackView(client, owner_id))


class CoreCommands(commands.Cog):
    diagnostics_group = app_commands.Group(name="bot", description="Состояние и диагностика NewSqadBot")

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

    @diagnostics_group.command(name="status", description="Проверить работу ядра бота")
    async def status_command(self, interaction: discord.Interaction) -> None:
        if not await self._authorized(interaction):
            return
        ok = await self.client.database.ping()
        await interaction.response.send_message(
            embed=embed_card("Состояние NewSqadBot", f"Версия: `{VERSION}`\nБаза данных: {'доступна' if ok else 'недоступна'}\nАктивных модулей: {sum(s == 'active' for s in self.client.module_states.values())}"),
            ephemeral=True,
        )

    @diagnostics_group.command(name="modules", description="Показать фактический статус модулей")
    async def modules_command(self, interaction: discord.Interaction) -> None:
        if not await self._authorized(interaction):
            return
        states = {"planned": "в планах", "active": "работает", "disabled": "отключён", "error": "ошибка"}
        lines = [f"• {m.title}: {states.get(self.client.module_states.get(m.key, 'planned'), 'неизвестно')}" for m in MODULES]
        await interaction.response.send_message(embed=embed_card("Модули NewSqadBot", "\n".join(lines)), ephemeral=True)
