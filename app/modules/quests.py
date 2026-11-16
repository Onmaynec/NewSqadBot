"""Ежедневные и еженедельные задания NewSquad."""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from app.core.permissions import can_manage_settings
from app.services import achievements as awards
from app.services import quests as store
from app.services.membership_access import approved_member
from app.ui.theme import LoggedView, card, notice

log = logging.getLogger(__name__)


def quest_embed(states: list[store.QuestState], frequency: str) -> discord.Embed:
    title = "Ежедневные задания" if frequency == "daily" else "Еженедельные задания"
    embed = card(f"NewSquad  /  {title}",
                 "Прогресс засчитывается только за реальную активность с XP. "
                 "Голосовой XP начисляется за совместное общение, без AFK.\n\n")
    blocks = []
    for state in states:
        quest = state.quest
        flag = "✅ Получено" if state.claimed else "🟢 Готово" if state.value >= quest.goal else "◦ В процессе"
        blocks.append(f"**{quest.title}** · {flag}\n{quest.description}\n"
                      f"`{state.value}/{quest.goal}` · **+{quest.xp} XP** · **+{quest.coins} NS Coins**\n"
                      f"Код: `{quest.code}`")
    embed.description += "\n\n".join(blocks)
    if states:
        embed.set_footer(text=f"Обновление: <t:{states[0].ends_at}:R> · NewSquad")
    return embed


class QuestBoardView(LoggedView):
    def __init__(self, cog: "Quests", user_id: int, frequency: str):
        super().__init__(timeout=240)
        self.cog = cog
        self.user_id = user_id
        self.frequency = frequency

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild_id != self.cog.bot.config.guild_id or interaction.user.id != self.user_id:
            await interaction.response.send_message(embed=notice("Эта панель принадлежит другому участнику.",
                                                                   kind="warning"), ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Сегодня", style=discord.ButtonStyle.primary)
    async def daily(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.cog.refresh_board(interaction, "daily")

    @discord.ui.button(label="Неделя", style=discord.ButtonStyle.secondary)
    async def weekly(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.cog.refresh_board(interaction, "weekly")

    @discord.ui.button(label="Получить награды", style=discord.ButtonStyle.primary)
    async def collect(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True)
        if not await self.cog.approved(interaction) or not await self.cog.bot.database.get_setting(
                interaction.guild_id, "quests_enabled", True) or not await self.cog.bot.database.get_setting(
                interaction.guild_id, f"quests_{self.frequency}_enabled", True):
            await interaction.followup.send(embed=notice("Доступ к этому разделу изменён. Откройте /quests заново.",
                                                        kind="warning"), ephemeral=True)
            return
        if not await self.cog.bot.database.get_setting(interaction.guild_id, "economy_enabled", True):
            await interaction.followup.send(embed=notice("Для получения наград должна быть включена экономика.",
                                                        kind="warning"), ephemeral=True)
            return
        states = await store.list_quests(self.cog.bot.database, interaction.guild_id, interaction.user.id,
                                         self.cog.bot.config.timezone, self.frequency)
        done = []
        for state in states:
            if state.claimed or state.value < state.quest.goal:
                continue
            try:
                await store.claim(self.cog.bot.database, guild_id=interaction.guild_id,
                                  user_id=interaction.user.id, code=state.quest.code,
                                  timezone=self.cog.bot.config.timezone)
                done.append(state.quest.title)
            except ValueError as exc:
                log.warning("Награда задания не получена user=%s code=%s: %s",
                            interaction.user.id, state.quest.code, exc)
            except Exception:
                log.exception("Ошибка награды user=%s quest=%s", interaction.user.id, state.quest.code)
                await interaction.followup.send(embed=notice("Не удалось завершить часть наград. "
                    "Проверьте журнал Docker.", kind="error"), ephemeral=True)
                return
        if done:
            await awards.sync(self.cog.bot.database, interaction.guild_id, interaction.user.id)
        await interaction.followup.send(embed=notice(
            f"Получено наград: {len(done)}." if done else "Пока нет выполненных заданий с невыплаченной наградой.",
            kind="success" if done else "info"), ephemeral=True)
        await self.cog.update_message(interaction, self.frequency)


class Quests(commands.Cog):
    quests = app_commands.Group(name="quests", description="Ежедневные и еженедельные задания NewSquad")

    def __init__(self, bot):
        self.bot = bot

    async def approved(self, interaction: discord.Interaction) -> bool:
        member = interaction.user
        if (interaction.guild_id != self.bot.config.guild_id or not isinstance(member, discord.Member)
                or member.bot or member.pending):
            return False
        role = await self.bot.database.get_setting(interaction.guild_id, "member_role_id")
        return await approved_member(self.bot.database, member, role)

    async def permitted(self, interaction: discord.Interaction, frequency: str | None = None) -> bool:
        if not await self.approved(interaction):
            await interaction.response.send_message(embed=notice(
                "Задания доступны после одобрения заявки NewSquad.", kind="warning"), ephemeral=True)
            return False
        if not await self.bot.database.get_setting(interaction.guild_id, "quests_enabled", True):
            await interaction.response.send_message(embed=notice("Задания временно отключены.", kind="warning"),
                                                    ephemeral=True)
            return False
        if frequency and not await self.bot.database.get_setting(interaction.guild_id,
                                                                   f"quests_{frequency}_enabled", True):
            await interaction.response.send_message(embed=notice("Этот тип заданий отключён.", kind="warning"),
                                                    ephemeral=True)
            return False
        return True

    async def admin(self, interaction: discord.Interaction) -> bool:
        if interaction.guild_id == self.bot.config.guild_id and can_manage_settings(
                interaction, self.bot.config.owner_ids):
            return True
        await interaction.response.send_message(embed=notice("Настройки доступны администрации.", kind="warning"),
                                                ephemeral=True)
        return False

    async def update_message(self, interaction: discord.Interaction, frequency: str) -> None:
        states = await store.list_quests(self.bot.database, interaction.guild_id, interaction.user.id,
                                         self.bot.config.timezone, frequency)
        await interaction.edit_original_response(embed=quest_embed(states, frequency),
                                                 view=QuestBoardView(self, interaction.user.id, frequency))

    async def refresh_board(self, interaction: discord.Interaction, frequency: str) -> None:
        if not await self.approved(interaction) or not await self.bot.database.get_setting(
                interaction.guild_id, "quests_enabled", True) or not await self.bot.database.get_setting(
                interaction.guild_id, f"quests_{frequency}_enabled", True):
            await interaction.response.send_message(embed=notice("Задания недоступны или отключены.", kind="warning"),
                                                    ephemeral=True)
            return
        await interaction.response.defer()
        await self.update_message(interaction, frequency)

    async def board(self, interaction: discord.Interaction, frequency: str) -> None:
        if not await self.permitted(interaction, frequency):
            return
        await interaction.response.defer(ephemeral=True)
        await self.update_message(interaction, frequency)

    @quests.command(name="daily", description="Ваши задания на сегодня")
    async def daily(self, interaction: discord.Interaction) -> None:
        await self.board(interaction, "daily")

    @quests.command(name="weekly", description="Ваши задания на неделю")
    async def weekly(self, interaction: discord.Interaction) -> None:
        await self.board(interaction, "weekly")

    @quests.command(name="claim", description="Получить награду за выполненное задание")
    async def claim(self, interaction: discord.Interaction, code: str) -> None:
        quest = store.BY_CODE.get(code)
        if quest is None:
            await interaction.response.send_message(embed=notice("Неверный код задания.", kind="warning"),
                                                    ephemeral=True)
            return
        if not await self.permitted(interaction, quest.frequency):
            return
        if not await self.bot.database.get_setting(interaction.guild_id, "economy_enabled", True):
            await interaction.response.send_message(embed=notice("Получение наград недоступно, пока экономика отключена.",
                                                                  kind="warning"), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            result = await store.claim(self.bot.database, guild_id=interaction.guild_id,
                                       user_id=interaction.user.id, code=code,
                                       timezone=self.bot.config.timezone)
            await awards.sync(self.bot.database, interaction.guild_id, interaction.user.id)
        except ValueError as exc:
            await interaction.followup.send(embed=notice(str(exc), kind="warning"), ephemeral=True)
            return
        await interaction.followup.send(embed=card("Награда за задание",
            f"**{quest.title}**\n+{result.xp} XP · +{result.coins} NS Coins"), ephemeral=True)

    @quests.command(name="history", description="История полученных наград за задания")
    async def history(self, interaction: discord.Interaction) -> None:
        if not await self.permitted(interaction):
            return
        records = await store.history(self.bot.database, interaction.guild_id, interaction.user.id)
        lines = [f"• `{x.code}` · <t:{x.claimed_at}:d> · +{x.xp_reward} XP / +{x.coin_reward} Coins"
                 for x in records]
        await interaction.response.send_message(embed=card("Награды за задания",
            "\n".join(lines) or "Пока ничего не получено."), ephemeral=True)

    @quests.command(name="settings", description="Настройки заданий")
    async def settings(self, interaction: discord.Interaction) -> None:
        if not await self.admin(interaction):
            return
        states = [(key, await self.bot.database.get_setting(interaction.guild_id, key, True))
                  for key in ("quests_enabled", "quests_daily_enabled", "quests_weekly_enabled")]
        lines = [f"**{key}:** {'включено' if value else 'выключено'}" for key, value in states]
        lines += [f"**Часовой пояс сброса:** {self.bot.config.timezone}",
                  "Награды: XP и NS Coins. Изменение заданий — в коде следующего релиза."]
        await interaction.response.send_message(embed=card("NewSquad  /  Задания", "\n".join(lines)), ephemeral=True)

    @quests.command(name="switch", description="Включить или выключить задания")
    @app_commands.choices(frequency=[app_commands.Choice(name="Все", value="all"),
                                    app_commands.Choice(name="Ежедневные", value="daily"),
                                    app_commands.Choice(name="Еженедельные", value="weekly")])
    async def switch(self, interaction: discord.Interaction, frequency: app_commands.Choice[str], enabled: bool) -> None:
        if not await self.admin(interaction):
            return
        key = "quests_enabled" if frequency.value == "all" else f"quests_{frequency.value}_enabled"
        await self.bot.database.set_setting(interaction.guild_id, key, enabled, actor_id=interaction.user.id)
        await interaction.response.send_message(embed=notice("Настройка заданий сохранена.", kind="success"),
                                                ephemeral=True)


async def setup(bot) -> None:
    await bot.add_cog(Quests(bot), guild=discord.Object(id=bot.config.guild_id))
