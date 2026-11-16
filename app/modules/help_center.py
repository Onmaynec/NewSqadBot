"""Интерактивный справочник /help по реально загруженным slash-командам."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import discord
from discord import app_commands
from discord.ext import commands

from app.config import VERSION
from app.ui.theme import LoggedView, NO_MENTIONS, card, notice

PAGE_SIZE = 8
CATEGORY_NAMES = {
    "help": "Справка",
    "ann": "Объявления",
    "application": "Моя заявка",
    "membership": "Приём участников",
    "bot": "Диагностика",
    "botsettings": "Настройки бота",
    "m": "Модерация",
    "welcome": "Приветствия",
    "roles": "Роли участников",
    "tickets": "Тикеты и поддержка",
    "audit": "Журнал событий",
    "levels": "Уровни и опыт",
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
    "ann", "membership", "m", "welcome", "roles",
    "audit", "bot", "botsettings",
)


@dataclass(frozen=True, slots=True)
class HelpEntry:
    name: str
    description: str

    @property
    def category(self) -> str:
        return self.name.split(" ", maxsplit=1)[0].removeprefix("/")


@dataclass(frozen=True, slots=True)
class HelpPage:
    category: str
    entries: tuple[HelpEntry, ...]


def collect_entries(tree: app_commands.CommandTree, guild_id: int) -> list[HelpEntry]:
    """Не перечисляем несуществующие команды: берём лишь регистрацию данной guild."""
    result: dict[str, HelpEntry] = {}
    guild = discord.Object(id=guild_id)
    for root in tree.get_commands(guild=guild):
        if isinstance(root, app_commands.Group):
            items = root.walk_commands()
        else:
            items = (root,)
        for item in items:
            if isinstance(item, app_commands.Command):
                name = f"/{item.qualified_name}"
                result[name] = HelpEntry(name=name, description=item.description or "Без описания")
    return sorted(result.values(), key=lambda entry: entry.name)


def build_pages(entries: list[HelpEntry]) -> list[HelpPage]:
    grouped: dict[str, list[HelpEntry]] = defaultdict(list)
    for entry in entries:
        grouped[entry.category].append(entry)
    categories = sorted(grouped, key=lambda name: (
        CATEGORY_ORDER.index(name) if name in CATEGORY_ORDER else len(CATEGORY_ORDER),
        name,
    ))
    pages = []
    for category in categories:
        commands = grouped[category]
        for offset in range(0, len(commands), PAGE_SIZE):
            pages.append(HelpPage(category, tuple(commands[offset:offset + PAGE_SIZE])))
    return pages


def page_embed(pages: list[HelpPage], index: int) -> discord.Embed:
    page = pages[index]
    total = sum(len(p.entries) for p in pages)
    lines = [
        f"**\u200b{discord.utils.escape_markdown(item.name)}**\n"
        f"{discord.utils.escape_mentions(item.description[:180])}"
        for item in page.entries
    ]
    title = CATEGORY_NAMES.get(page.category, f"/{page.category}")
    embed = card(
        f"NewSquad  /  Команды  /  {title}",
        "**Справочник команд NewSqadBot**\n"
        "Выберите раздел ниже или листайте страницы кнопками.\n\n"
        + "\n\n".join(lines),
    )
    embed.set_footer(text=(
        f"Страница {index + 1} / {len(pages)}  •  Команд: {total}  •  "
        f"Версия {VERSION}  •  Доступ зависит от прав участника"
    ))
    return embed


class HelpCategorySelect(discord.ui.Select):
    def __init__(self, view: HelpView):
        self.help_view = view
        first_page: dict[str, int] = {}
        for i, page in enumerate(view.pages):
            first_page.setdefault(page.category, i)
        self.first_page = first_page
        options = [
            discord.SelectOption(
                label=CATEGORY_NAMES.get(name, f"/{name}")[:100],
                description=f"Команды раздела /{name}"[:100],
                value=str(i),
                default=i == 0,
            )
            for name, i in list(first_page.items())[:25]
        ]
        super().__init__(
            placeholder="Быстрый переход к разделу", options=options,
            min_values=1, max_values=1, row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        self.help_view.page = int(self.values[0])
        await self.help_view.update(interaction)


class HelpView(LoggedView):
    def __init__(self, pages: list[HelpPage], owner_id: int):
        super().__init__(timeout=600)
        self.pages = pages
        self.owner_id = owner_id
        self.page = 0
        self.jump = HelpCategorySelect(self)
        if len({p.category for p in pages}) > 1:
            self.add_item(self.jump)
        self.sync()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not await super().interaction_check(interaction):
            return False
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                embed=notice("Эта справка открыта другим участником.", kind="warning"),
                ephemeral=True,
            )
            return False
        return True

    def sync(self) -> None:
        self.previous.disabled = self.page == 0
        self.next_page.disabled = self.page == len(self.pages) - 1
        for option in self.jump.options:
            selected = int(option.value)
            option.default = self.pages[selected].category == self.pages[self.page].category

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
            embed=page_embed(self.pages, self.page), view=None,
            allowed_mentions=NO_MENTIONS,
        )


class HelpCenter(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="help", description="Открыть справочник всех команд бота")
    async def help(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id != self.bot.config.guild_id:
            await interaction.response.send_message(
                embed=notice("Справочник доступен только в NewSquad.", kind="warning"),
                ephemeral=True,
            )
            return
        entries = collect_entries(self.bot.tree, self.bot.config.guild_id)
        pages = build_pages(entries)
        if not pages:
            await interaction.response.send_message(
                embed=notice("Команды пока недоступны. Администрация проверит загрузку модулей.",
                             kind="warning"), ephemeral=True,
            )
            return
        await interaction.response.send_message(
            embed=page_embed(pages, 0),
            view=HelpView(pages, interaction.user.id),
            ephemeral=True,
            allowed_mentions=NO_MENTIONS,
        )


async def setup(bot) -> None:
    await bot.add_cog(HelpCenter(bot), guild=discord.Object(id=bot.config.guild_id))
