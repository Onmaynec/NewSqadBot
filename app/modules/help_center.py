"""Интерактивный справочник реальных slash-команд с поиском и правами."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import discord
from discord import app_commands
from discord.ext import commands

from app.config import VERSION
from app.core.permissions import can_manage_settings
from app.services.membership_access import approved_member
from app.ui.theme import NO_MENTIONS, LoggedModal, LoggedView, card, notice, panel_card

PAGE_SIZE = 8
CATEGORY_NAMES = {
    "help": "Справка",
    "ann": "Объявления",
    "announcements": "Центр объявлений",
    "application": "Моя заявка",
    "membership": "Приём участников",
    "bot": "Диагностика",
    "botsettings": "Настройки бота",
    "m": "Модерация",
    "welcome": "Приветствия",
    "roles": "Роли",
    "tickets": "Тикеты",
    "audit": "Аудит",
    "levels": "Уровни",
    "economy": "Экономика",
    "shop": "Магазин",
    "wallet": "Кошелёк",
    "voice": "Голосовые комнаты",
    "party": "Поиск команды",
    "events": "Мероприятия",
    "event": "Мероприятия",
    "giveaway": "Розыгрыши",
    "stats": "Статистика",
    "achievements": "Достижения",
    "quests": "Задания",
    "profile": "Профили",
    "profiles": "Профили",
    "suggest": "Предложения",
}
CATEGORY_ORDER = (
    "help", "application", "profile", "profiles", "levels", "wallet",
    "economy", "shop", "voice", "party", "events", "event", "giveaway",
    "suggest", "quests", "achievements", "stats", "tickets",
    "ann", "announcements", "membership", "m", "welcome", "roles",
    "audit", "bot", "botsettings",
)
ADMIN_ROOTS = frozenset({"ann", "announcements", "botsettings", "m", "membership", "audit"})
ADMIN_SUBCOMMANDS = {
    "/suggest configure", "/suggest status", "/suggest settings", "/suggest panel",
    "/suggest restore", "/stats configure", "/stats settings", "/stats panel",
    "/event create", "/event cancel", "/event confirm",
    "/giveaway create", "/giveaway end", "/giveaway reroll",
}


@dataclass(frozen=True, slots=True)
class HelpEntry:
    name: str
    description: str
    arguments: tuple[tuple[str, str, bool], ...] = ()

    @property
    def category(self) -> str:
        return self.name.split(" ", maxsplit=1)[0].removeprefix("/")


@dataclass(frozen=True, slots=True)
class HelpPage:
    category: str
    entries: tuple[HelpEntry, ...]


def collect_entries(tree: app_commands.CommandTree, guild_id: int) -> list[HelpEntry]:
    result: dict[str, HelpEntry] = {}
    guild = discord.Object(id=guild_id)
    for root in tree.get_commands(guild=guild):
        items = root.walk_commands() if isinstance(root, app_commands.Group) else (root,)
        for item in items:
            if isinstance(item, app_commands.Command):
                name = f"/{item.qualified_name}"
                params = tuple((
                    p.name, p.description or "Параметр", p.required,
                ) for p in item.parameters)
                result[name] = HelpEntry(name=name, description=item.description or "Без описания",
                                         arguments=params)
    return sorted(result.values(), key=lambda x: x.name)


def visible_entries(entries: list[HelpEntry], *, admin: bool,
                    authorized: bool) -> list[HelpEntry]:
    """Категории не заменяют проверку прав самой команды."""
    if admin:
        return list(entries)
    if not authorized:
        return [e for e in entries if e.category in ("help", "application")]
    return [e for e in entries if e.category not in ADMIN_ROOTS
            and e.name not in ADMIN_SUBCOMMANDS]


def find_entries(entries: list[HelpEntry], query: str) -> list[HelpEntry]:
    query = query.strip().lower().removeprefix("/")
    if not query:
        return []
    return [entry for entry in entries
            if query in entry.name.lower() or query in entry.description.lower()][:15]


def detail_embed(entry: HelpEntry) -> discord.Embed:
    args = " ".join(
        f"<{name}>" if required else f"[{name}]" for name, _, required in entry.arguments
    )
    lines = [
        f"**Команда:** {discord.utils.escape_markdown(entry.name)} {args}",
        f"**Назначение:** {discord.utils.escape_mentions(entry.description)}",
    ]
    if entry.arguments:
        lines.append("**Параметры**")
        for name, description, required in entry.arguments:
            label = "обязательный" if required else "необязательный"
            lines.append(f"- **{name}** ({label}) — {discord.utils.escape_mentions(description)}")
    lines.append("\nУточняйте доступные параметры прямо в списке команд Discord.")
    return card("NewSquad / Подробности команды", "\n".join(lines))


def build_pages(entries: list[HelpEntry]) -> list[HelpPage]:
    grouped: dict[str, list[HelpEntry]] = defaultdict(list)
    for entry in entries:
        grouped[entry.category].append(entry)
    categories = sorted(grouped, key=lambda name: (
        CATEGORY_ORDER.index(name) if name in CATEGORY_ORDER else len(CATEGORY_ORDER), name,
    ))
    pages: list[HelpPage] = []
    for category in categories:
        for offset in range(0, len(grouped[category]), PAGE_SIZE):
            pages.append(HelpPage(category, tuple(grouped[category][offset:offset + PAGE_SIZE])))
    return pages


def page_embed(pages: list[HelpPage], index: int) -> discord.Embed:
    page = pages[index]
    total = sum(len(p.entries) for p in pages)
    lines = [
        f"**{discord.utils.escape_markdown(item.name)}**\n"
        f"{discord.utils.escape_mentions(item.description[:180])}"
        for item in page.entries
    ]
    title = CATEGORY_NAMES.get(page.category, f"/{page.category}")
    embed = panel_card(
        f"NewSquad  /  Справка  /  {title}",
        "Выберите раздел в меню или найдите нужную команду через поиск.",
        ("Команды раздела", "\n\n".join(lines)),
    )
    embed.set_footer(text=(
        f"Страница {index + 1} / {len(pages)}  •  Команд: {total}  •  "
        f"Версия {VERSION}  •  Доступ проверяется при выполнении"
    ))
    return embed


class HelpCategorySelect(discord.ui.Select):
    def __init__(self, view: HelpView):
        self.help_view = view
        self.first_pages: dict[str, int] = {}
        for i, page in enumerate(view.pages):
            self.first_pages.setdefault(page.category, i)
        super().__init__(placeholder="Перейти к разделу", options=[
            discord.SelectOption(label="Раздел", value="0"),
        ], row=2)
        self.rebuild()

    def rebuild(self) -> None:
        items = list(self.first_pages.items())
        left = self.help_view.category_offset
        options = []
        for name, index in items[left:left + 25]:
            options.append(discord.SelectOption(
                label=CATEGORY_NAMES.get(name, f"/{name}")[:100],
                description=f"Команды /{name}"[:100],
                value=str(index),
                default=name == self.help_view.pages[self.help_view.page].category,
            ))
        self.options = options

    async def callback(self, interaction: discord.Interaction) -> None:
        self.help_view.page = int(self.values[0])
        await self.help_view.update(interaction)


class HelpSearchModal(LoggedModal):
    def __init__(self, view: HelpView):
        super().__init__(title="Найти команду NewSquad", timeout=300)
        self.view = view
        self.query = discord.ui.TextInput(
            min_length=1, max_length=80, placeholder="Например: profile edit",
        )
        self.add_item(discord.ui.Label(text="Команда или назначение", component=self.query))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.view.owner_id:
            await interaction.response.send_message(
                embed=notice("Справка открыта другим пользователем.", kind="warning"),
                ephemeral=True)
            return
        entries = [e for page in self.view.pages for e in page.entries]
        matches = find_entries(entries, str(self.query.value))
        if not matches:
            await interaction.response.send_message(
                embed=notice("Команды по запросу не найдены.", kind="warning"), ephemeral=True)
            return
        if len(matches) == 1:
            embed = detail_embed(matches[0])
        else:
            embed = card("NewSquad / Результаты поиска", "\n".join(
                f"**{e.name}** — {discord.utils.escape_mentions(e.description[:130])}"
                for e in matches))
            embed.set_footer(text="Уточните запрос, чтобы открыть подробности команды.")
        await interaction.response.send_message(embed=embed, ephemeral=True,
                                                allowed_mentions=NO_MENTIONS)


class HelpView(LoggedView):
    def __init__(self, pages: list[HelpPage], owner_id: int):
        super().__init__(timeout=600)
        self.pages, self.owner_id = pages, owner_id
        self.page = 0
        self.category_offset = 0
        self.jump = HelpCategorySelect(self)
        if len({page.category for page in pages}) > 1:
            self.add_item(self.jump)
        self.sync()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not await super().interaction_check(interaction):
            return False
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                embed=notice("Эта справка открыта другим участником.", kind="warning"),
                ephemeral=True)
            return False
        return True

    def sync(self) -> None:
        self.previous.disabled = self.page == 0
        self.next_page.disabled = self.page == len(self.pages) - 1
        categories = list(self.jump.first_pages)
        current = self.pages[self.page].category
        if current in categories:
            pos = categories.index(current)
            if not self.category_offset <= pos < self.category_offset + 25:
                self.category_offset = pos // 25 * 25
        self.previous_categories.disabled = self.category_offset == 0
        self.next_categories.disabled = self.category_offset + 25 >= len(categories)
        self.jump.rebuild()

    async def update(self, interaction: discord.Interaction) -> None:
        self.sync()
        await interaction.response.edit_message(
            embed=page_embed(self.pages, self.page), view=self,
            allowed_mentions=NO_MENTIONS,
        )

    @discord.ui.button(label="Назад", emoji="◀️", style=discord.ButtonStyle.secondary, row=0)
    async def previous(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.page = max(0, self.page - 1)
        await self.update(interaction)

    @discord.ui.button(label="Далее", emoji="▶️", style=discord.ButtonStyle.primary, row=0)
    async def next_page(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.page = min(len(self.pages) - 1, self.page + 1)
        await self.update(interaction)

    @discord.ui.button(label="Закрыть", emoji="✖️", style=discord.ButtonStyle.secondary, row=0)
    async def close(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.stop()
        await interaction.response.edit_message(
            embed=page_embed(self.pages, self.page), view=None, allowed_mentions=NO_MENTIONS,
        )

    @discord.ui.button(label="Пред. разделы", style=discord.ButtonStyle.secondary, row=1)
    async def previous_categories(self, interaction: discord.Interaction,
                                  _: discord.ui.Button) -> None:
        self.category_offset = max(0, self.category_offset - 25)
        self.page = list(self.jump.first_pages.values())[self.category_offset]
        await self.update(interaction)

    @discord.ui.button(label="След. разделы", style=discord.ButtonStyle.secondary, row=1)
    async def next_categories(self, interaction: discord.Interaction,
                              _: discord.ui.Button) -> None:
        self.category_offset += 25
        self.page = list(self.jump.first_pages.values())[self.category_offset]
        await self.update(interaction)

    @discord.ui.button(label="Найти команду", emoji="🔎", style=discord.ButtonStyle.primary, row=1)
    async def search(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(HelpSearchModal(self))


class HelpCenter(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="help", description="Интерактивный справочник команд")
    async def help(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id != self.bot.config.guild_id:
            await interaction.response.send_message(
                embed=notice("Справочник доступен только в NewSquad.", kind="warning"),
                ephemeral=True)
            return
        entries = collect_entries(self.bot.tree, self.bot.config.guild_id)
        is_admin = can_manage_settings(interaction, self.bot.config.owner_ids)
        candidate_id = await self.bot.database.get_setting(interaction.guild_id, "candidate_role_id")
        authorized = await approved_member(self.bot.database, interaction.user, candidate_id)
        entries = visible_entries(entries, admin=is_admin, authorized=authorized)
        pages = build_pages(entries)
        if not pages:
            await interaction.response.send_message(
                embed=notice("Доступных команд пока нет.", kind="warning"), ephemeral=True)
            return
        await interaction.response.send_message(
            embed=page_embed(pages, 0), view=HelpView(pages, interaction.user.id),
            ephemeral=True, allowed_mentions=NO_MENTIONS,
        )


async def setup(bot) -> None:
    await bot.add_cog(HelpCenter(bot), guild=discord.Object(id=bot.config.guild_id))
