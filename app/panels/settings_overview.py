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

    @discord.ui.button(label="Обновить", emoji="🔄", style=discord.ButtonStyle.success, row=1)
    async def reload(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.refresh(interaction)

    @discord.ui.button(label="Главная", emoji="↩️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        from app.panels.botsettings import HomeView, home_embed
        await interaction.response.edit_message(embed=home_embed(), view=HomeView(self.bot, self.owner_id))
