"""Единый просмотр текущих настроек NewSquad без доступа к .env или токенам."""
from __future__ import annotations

import json

import discord
from sqlalchemy import select

from app.config import FEMALE_ROLE_ID, VERSION
from app.core.catalog import MODULES
from app.core.permissions import can_manage_settings
from app.models import GuildSetting, TicketCategory
from app.ui.theme import LoggedView, card

PAGE_OPTIONS = (
    ("summary", "Обзор", "Статус и заполненность настроек"),
    ("roles", "Роли", "Все назначенные роли Discord"),
    ("channels", "Каналы", "Каналы и категория приватных тикетов"),
    ("tickets", "Тикеты", "Параметры и категории обращений"),
    ("voice", "Голосовые", "Лобби, категория и живые комнаты"),
    ("leveling", "Уровни", "XP, лимиты и исключённые каналы"),
    ("economy", "Экономика", "Баланс, награды, магазин и переводы"),
    ("quests", "Задания", "Дневные и недельные задания, награды и сброс"),
    ("achievements", "Достижения", "Постоянные достижения и их награды"),
    ("profiles", "Профили", "Правила и приватность профилей"),
    ("suggestions", "Предложения", "Канал, голоса и антиспам"),
    ("statistics", "Статистика", "Агрегаты, рейтинги и приватность"),
    ("modules", "Модули", "Фактическое состояние всех модулей"),
    ("all", "Все параметры", "Все ключи настройки с постраничным просмотром"),
)
PAGE_SIZE = 12
HIDDEN_TERMS = ("token", "password", "secret", "authorization", "private_key")


def safe_value(value: object, *, limit: int = 110) -> str:
    raw = str(value).replace("\n", " ").replace("`", "'")
    return discord.utils.escape_mentions(raw[:limit] + ("…" if len(raw) > limit else ""))


async def current_settings(bot) -> dict:
    async with bot.database.sessions() as session:
        rows = (await session.scalars(select(GuildSetting).where(
            GuildSetting.guild_id == bot.config.guild_id))).all()
    return {row.key: json.loads(row.value_json) for row in rows}


async def overview_embed(bot, page: str, offset: int = 0) -> tuple[discord.Embed, int]:
    from app.panels.botsettings import CHANNEL_SLOTS, ROLE_SLOTS

    settings = await current_settings(bot)
    guild = bot.get_guild(bot.config.guild_id)
    roles = dict(ROLE_SLOTS)
    channels = dict(CHANNEL_SLOTS)
    pages = [p[0] for p in PAGE_OPTIONS]
    if page not in pages:
        page = "summary"
    title = dict((key, label) for key, label, _ in PAGE_OPTIONS)[page]
    e = card(f"NewSquad  /  Настройки  /  {title}", "")
    lines: list[str] = []
    total_pages = 1
    if page == "summary":
        db_ok = await bot.database.ping()
        assigned_roles = sum(bool(settings.get(k)) for k in roles)
        assigned_channels = sum(bool(settings.get(k)) for k in channels)
        active_modules = sum(state == "active" for state in bot.module_states.values())
        lines = [
            f"**Версия:** {VERSION}",
            f"**Сервер:** NewSquad (`{bot.config.guild_id}`)",
            f"**Подключение:** {'● работает' if bot.is_ready() else '○ не готово'}",
            f"**База данных:** {'● доступна' if db_ok else '○ ошибка'}",
            f"**Модули:** {active_modules} активных из {len(MODULES)}",
            f"**Роли:** назначено {assigned_roles} из {len(roles)}",
            f"**Каналы/категории:** назначено {assigned_channels} из {len(channels)}",
            f"**Сохранённых параметров:** {len(settings)}",
            "",
            "Выберите раздел в меню ниже, чтобы посмотреть все назначенные ID, "
            "состояния модулей и сохранённые параметры.",
        ]
    elif page == "roles":
        for key, label in ROLE_SLOTS:
            rid = settings.get(key, FEMALE_ROLE_ID if key == "female_role_id" else None)
            role = guild.get_role(rid) if guild and rid else None
            line = f"**{label}** — " + (f"{discord.utils.escape_markdown(role.name)} (`{rid}`) ✅"
                                             if role else "⚠️ не настроена" if not rid
                                             else f"⚠️ ID `{rid}` не найден на сервере")
            lines.append(line)
        lines.append("\nРоль кандидата должна находиться **ниже** роли NewSqadBot для автоматического снятия.")
    elif page == "channels":
        for key, label in CHANNEL_SLOTS:
            cid = settings.get(key)
            channel = guild.get_channel(cid) if guild and cid else None
            if channel:
                description = f"**{discord.utils.escape_markdown(channel.name)}** (`{cid}`) ✅"
            else:
                description = "⚠️ не выбран" if not cid else f"⚠️ ID `{cid}` не найден"
            lines.append(f"**{label}** — {description}")
        lines.append("\nКатегория тикетов — именно **категория Discord**, не текстовый канал.")
    elif page == "tickets":
        lines = [
            f"**Приём новых обращений:** {'включён' if settings.get('tickets_enabled', True) else 'выключен'}",
            f"**Лимит на участника:** {settings.get('tickets_max_open', 3)}",
            f"**Автозакрытие через:** {settings.get('tickets_inactivity_days', 0)} дней (0 — отключено)",
            "**Закрытие:** удаление канала и данных без сохранения и экспорта",
            "**Журнал тикетов:** отключён",
            "",
            "**Категории обращений**",
        ]
        async with bot.database.sessions() as session:
            cats = (await session.scalars(select(TicketCategory).where(
                TicketCategory.guild_id == bot.config.guild_id).order_by(TicketCategory.sort_order))).all()
        for c in cats:
            lines.append(f"• **{safe_value(c.title, limit=50)}** · {'активна' if c.enabled else 'выключена'}"
                         f" · {('роль ' + str(c.support_role_id)) if c.support_role_id else 'общая поддержка'}")
        if not cats:
            lines.append("Категории не созданы")
    elif page == "voice":
        from app.services import voice as rooms_store

        rooms = await rooms_store.all_rooms(bot.database, bot.config.guild_id)
        lines = [
            f"**Включены:** {'да' if settings.get('voice_enabled', True) else 'нет'}",
            f"**Канал создания:** {f'<#{settings.get("voice_lobby_channel_id")}>' if settings.get('voice_lobby_channel_id') else 'не выбран'}",
            f"**Категория комнат:** {settings.get('voice_category_channel_id') or 'не выбрана'}",
            f"**Комнат:** {len(rooms)} / {settings.get('voice_max_rooms', 30)}",
            f"**Лимит участников:** {settings.get('voice_default_limit', 0) or 'без лимита'}",
            "", "Настроить: `/voice setup`, `/voice configure`.",
            "Панель владельца: `/voice panel`.",
        ]
    elif page == "leveling":
        values = {
            "leveling_enabled": ("Система", True),
            "leveling_text_enabled": ("Текстовый XP", True),
            "leveling_voice_enabled": ("Голосовой XP", True),
            "leveling_message_xp": ("За сообщение", 20),
            "leveling_voice_xp": ("За 10 голосовых минут", 30),
            "leveling_cooldown_seconds": ("Задержка, сек", 60),
            "leveling_daily_cap": ("Дневной лимит XP", 1200),
            "leveling_excluded_channel_ids": ("Исключённых каналов", []),
        }
        for key, (label, default) in values.items():
            val = settings.get(key, default)
            if isinstance(default, bool):
                val = "включено" if val else "выключено"
            if isinstance(val, list):
                val = len(val)
            lines.append(f"**{label}:** {val}")
        lines.append("\nИзменения: `/levels settings` и `/botsettings` → «Уровни и XP».")
    elif page == "economy":
        config = (("economy_enabled", "Экономика", True),
                  ("economy_transfers_enabled", "Переводы", True),
                  ("economy_activity_enabled", "Выплаты за XP", True),
                  ("economy_daily_reward", "Ежедневная награда", 100),
                  ("economy_message_reward", "За текстовое сообщение с XP", 2),
                  ("economy_voice_reward", "За голосовой XP", 5),
                  ("economy_level_reward", "За достижение уровня", 25))
        for key, label, default in config:
            value = settings.get(key, default)
            lines.append(f"**{label}:** {value if not isinstance(default, bool) else ('включено' if value else 'выключено')}")
        lines += ["", "Управление: `/economy settings` / `/shop create` / `/economy rates`.",
                  "Валютные операции сохраняются в журнале SQLite."]
    elif page == "quests":
        lines = [
            f"**Задания:** {'включены' if settings.get('quests_enabled', True) else 'отключены'}",
            f"**Ежедневные:** {'включены' if settings.get('quests_daily_enabled', True) else 'отключены'}",
            f"**Еженедельные:** {'включены' if settings.get('quests_weekly_enabled', True) else 'отключены'}",
            f"**Часовой пояс:** {bot.config.timezone}",
            "**Учёт:** сообщения и голосовые минуты с подтверждённым XP.",
            "**Награды:** один раз за задание в пределах периода.",
            "", "Управление: `/quests settings` / `/quests switch`.",
        ]
    elif page == "achievements":
        lines = [
            f"**Достижения:** {'включены' if settings.get('achievements_enabled', True) else 'отключены'}",
            "**Открытие:** по фактической статистике без повторов.",
            "**Редкости:** обычная, необычная, редкая, эпическая.",
            "**Скрытые:** условия не показываются до открытия.",
            "**Награды:** XP и NS Coins, одна выплата навсегда.",
            "", "Управление: `/achievements settings` / `/achievements switch`.",
        ]
    elif page == "profiles":
        lines = [
            "**Система профилей:** доступна одобренным членам NewSquad.",
            "**Поля:** описание, игры, интересы, статус поиска компании.",
            "**Приватность:** каждый участник может скрыть свой профиль.",
            "**Анкеты Membership:** возраст и ответы не попадают в публичный профиль.",
            "", "Команды: `/profile view`, `/profile edit`, `/profile status`, `/profile privacy`.",
        ]
    elif page == "suggestions":
        from app.services import suggestions as suggestions_store

        lost, lost_panels = await suggestions_store.missing_count(
            bot.database, bot.config.guild_id)
        channel_id = settings.get("suggest_channel_id")
        lines = [
            f"**Система:** {'включена' if settings.get('suggestions_enabled', True) else 'отключена'}",
            f"**Канал:** {f'<#{channel_id}>' if channel_id else 'не выбран'}",
            f"**Минимальный уровень:** {settings.get('suggest_min_level', 1)}",
            f"**Cooldown:** {settings.get('suggest_cooldown_seconds', 1800) // 60} минут",
            f"**Удалённые карточки:** {lost} · **панели:** {lost_panels}",
            "", "Настройка: /suggest configure; обзор: /suggest settings.",
        ]
    elif page == "statistics":
        from app.services import statistics as stats_store

        panels = await stats_store.panels(bot.database, bot.config.guild_id)
        lines = [
            f"**Система:** {'включена' if settings.get('statistics_enabled', True) else 'отключена'}",
            f"**Учёт сообщений:** {'да' if settings.get('statistics_messages_enabled', True) else 'нет'}",
            f"**Учёт голоса:** {'да' if settings.get('statistics_voice_enabled', True) else 'нет'}",
            f"**Публичные рейтинги:** {'да' if settings.get('statistics_public_ranking', True) else 'нет'}",
            f"**Часовой пояс:** {settings.get('statistics_timezone', bot.config.timezone)}",
            f"**Панелей:** {len(panels)}",
            "", "Команды: /stats settings, /stats configure, /stats panel.",
        ]
    elif page == "modules":
        states = {"planned": "в планах", "active": "работает", "disabled": "выключен", "error": "ошибка загрузки"}
        lines = [f"{'✅' if bot.module_states.get(m.key) == 'active' else '▫️'} **{m.title}** "
                 f"— {states.get(bot.module_states.get(m.key, 'planned'), 'неизвестно')}" for m in MODULES]
    elif page == "all":
        keys = sorted(k for k in settings if not any(term in k.lower() for term in HIDDEN_TERMS))
        total_pages = max(1, (len(keys) + PAGE_SIZE - 1) // PAGE_SIZE)
        offset = max(0, min(offset, total_pages - 1))
        subset = keys[offset * PAGE_SIZE:(offset + 1) * PAGE_SIZE]
        lines = [f"**{safe_value(key, limit=70)}**\n`{safe_value(settings[key])}`" for key in subset]
        if not lines:
            lines = ["Изменённые настройки пока отсутствуют."]
        lines.append(f"\nСтраница **{offset + 1}/{total_pages}** · Параметров: {len(keys)}")
    e.description = "\n".join(lines)[:3900]
    e.set_footer(text="NewSquad • Доступ только администрации • Значения из базы бота")
    return e, total_pages


class PageSelector(discord.ui.Select):
    def __init__(self, view: "SettingsOverviewView"):
        super().__init__(placeholder="Выберите раздел настроек", min_values=1, max_values=1,
                         options=[discord.SelectOption(label=title, value=key, description=description,
                                                      default=(key == view.page)) for key, title, description in PAGE_OPTIONS],
                         row=0)
        self.dashboard = view

    async def callback(self, interaction: discord.Interaction) -> None:
        self.dashboard.page, self.dashboard.page_number = self.values[0], 0
        await self.dashboard.refresh(interaction)


class SettingsOverviewView(LoggedView):
    def __init__(self, bot, owner_id: int, *, page: str = "summary", page_number: int = 0):
        super().__init__(timeout=600)
        self.bot, self.owner_id, self.page, self.page_number = bot, owner_id, page, page_number
        self.max_pages = 1
        self.selector = PageSelector(self)
        self.add_item(self.selector)
        self._buttons()

    def _buttons(self) -> None:
        self.previous.disabled = self.page != "all" or self.page_number == 0
        self.next.disabled = self.page != "all" or self.page_number >= self.max_pages - 1

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not await super().interaction_check(interaction):
            return False
        if interaction.user.id != self.owner_id or not can_manage_settings(interaction, self.bot.config.owner_ids):
            await interaction.response.send_message("Эта панель доступна только тому, кто её открыл.", ephemeral=True)
            return False
        return True

    async def refresh(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        embed, total = await overview_embed(self.bot, self.page, self.page_number)
        self.max_pages = total
        self._buttons()
        for option in self.selector.options:
            option.default = option.value == self.page
        await interaction.edit_original_response(embed=embed, view=self)

    @discord.ui.button(label="Назад", emoji="◀️", style=discord.ButtonStyle.secondary, row=1)
    async def previous(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.page_number = max(0, self.page_number - 1)
        await self.refresh(interaction)

    @discord.ui.button(label="Вперёд", emoji="▶️", style=discord.ButtonStyle.secondary, row=1)
    async def next(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.page_number += 1
        await self.refresh(interaction)

    @discord.ui.button(label="Обновить", emoji="🔄", style=discord.ButtonStyle.primary, row=1)
    async def reload(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.refresh(interaction)

    @discord.ui.button(label="Главная", emoji="↩️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        from app.panels.botsettings import HomeView, home_embed
        await interaction.response.edit_message(embed=home_embed(), view=HomeView(self.bot, self.owner_id))
