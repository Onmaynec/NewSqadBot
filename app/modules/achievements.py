"""Постоянные достижения NewSquad за активность, уровни и выполненные задания."""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from app.core.permissions import can_manage_settings
from app.services import achievements as store
from app.services.profiles import get_profile
from app.services.membership_access import approved_member
from app.ui.theme import LoggedView, card, notice

log = logging.getLogger(__name__)
RARITIES = {"common": "Обычное", "uncommon": "Необычное", "rare": "Редкое", "epic": "Эпическое"}


def achievement_embed(states: list[store.AchievementState], *, own: bool) -> discord.Embed:
    lines = []
    for state in states:
        a = state.achievement
        if a.hidden and not state.unlocked_at:
            if own:
                lines.append("**Скрытое достижение** · 🔒 Условия неизвестны")
            continue
        flag = "✅ Награда получена" if state.claimed_at else "🟢 Награда доступна" if state.unlocked_at else "◦ В процессе"
        if not own and not state.unlocked_at:
            continue
        text = f"**{a.title}** · {RARITIES[a.rarity]} · {flag}\n{a.description}"
        if own:
            text += f"\n`{state.value}/{a.goal}` · +{a.xp} XP / +{a.coins} NS Coins · `{a.code}`"
        lines.append(text)
    return card("NewSquad  /  Достижения", "\n\n".join(lines) or "Пока достижений нет.")


class AchievementBoardView(LoggedView):
    def __init__(self, cog: "Achievements", user_id: int):
        super().__init__(timeout=240)
        self.cog = cog
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild_id != self.cog.bot.config.guild_id or interaction.user.id != self.user_id:
            await interaction.response.send_message(embed=notice("Это панель другого участника.", kind="warning"),
                                                    ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Обновить", style=discord.ButtonStyle.secondary)
    async def refresh(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.defer()
        await self.cog.update_message(interaction)

    @discord.ui.button(label="Получить доступные награды", style=discord.ButtonStyle.success)
    async def collect(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True)
        if not await self.cog.bot.database.get_setting(interaction.guild_id, "achievements_enabled", True) or (
                not isinstance(interaction.user, discord.Member) or not await self.cog.approved(interaction.user)):
            await interaction.followup.send(embed=notice("Достижения недоступны. Откройте панель заново.",
                                                        kind="warning"), ephemeral=True)
            return
        if not await self.cog.bot.database.get_setting(interaction.guild_id, "economy_enabled", True):
            await interaction.followup.send(embed=notice("Экономика отключена. Награды пока недоступны.",
                                                         kind="warning"), ephemeral=True)
            return
        await store.sync(self.cog.bot.database, interaction.guild_id, interaction.user.id)
        rows = await store.list_achievements(self.cog.bot.database, interaction.guild_id, interaction.user.id)
        received = []
        for state in rows:
            if not state.unlocked_at or state.claimed_at:
                continue
            try:
                await store.claim(self.cog.bot.database, guild_id=interaction.guild_id,
                                  user_id=interaction.user.id, code=state.achievement.code,
                                  timezone=self.cog.bot.config.timezone)
                received.append(state.achievement.title)
            except ValueError as exc:
                log.warning("Ошибка получения достижения user=%s code=%s: %s",
                            interaction.user.id, state.achievement.code, exc)
            except Exception:
                log.exception("Ошибка награды достижения user=%s code=%s", interaction.user.id,
                              state.achievement.code)
                await interaction.followup.send(embed=notice("Ошибка при выдаче наград. "
                    "Подробности в Docker-логах.", kind="error"), ephemeral=True)
                return
        await interaction.followup.send(embed=notice(
            f"Получено наград: {len(received)}." if received else "Все доступные награды уже получены.",
            kind="success" if received else "info"), ephemeral=True)
        await self.cog.update_message(interaction)


class Achievements(commands.Cog):
    achievements = app_commands.Group(name="achievements", description="Постоянные достижения NewSquad")

    def __init__(self, bot):
        self.bot = bot
        bot.events.subscribe("leveling.xp_awarded", "achievements.xp", self.on_progress)
        bot.events.subscribe("leveling.level_up", "achievements.level", self.on_progress)

    async def on_progress(self, payload: dict) -> None:
        guild_id, user_id = int(payload["guild_id"]), int(payload["user_id"])
        if guild_id != self.bot.config.guild_id or not await self.bot.database.get_setting(
                guild_id, "achievements_enabled", True):
            return
        unlocked = await store.sync(self.bot.database, guild_id, user_id)
        if unlocked:
            log.info("Открыты достижения guild=%s user=%s codes=%s", guild_id, user_id, ",".join(unlocked))

    async def approved(self, member: discord.Member) -> bool:
        if member.guild.id != self.bot.config.guild_id or member.bot or member.pending:
            return False
        role_id = await self.bot.database.get_setting(member.guild.id, "member_role_id")
        return await approved_member(self.bot.database, member, role_id)

    async def user_check(self, interaction: discord.Interaction) -> bool:
        if (interaction.guild_id == self.bot.config.guild_id and isinstance(interaction.user, discord.Member)
                and await self.approved(interaction.user)):
            return True
        await interaction.response.send_message(embed=notice("Достижения доступны участникам NewSquad.",
                                                              kind="warning"), ephemeral=True)
        return False

    async def admin(self, interaction: discord.Interaction) -> bool:
        if interaction.guild_id == self.bot.config.guild_id and can_manage_settings(
                interaction, self.bot.config.owner_ids):
            return True
        await interaction.response.send_message(embed=notice("Только администрация может менять настройки.",
                                                              kind="warning"), ephemeral=True)
        return False

    async def update_message(self, interaction: discord.Interaction) -> None:
        await store.sync(self.bot.database, interaction.guild_id, interaction.user.id)
        states = await store.list_achievements(self.bot.database, interaction.guild_id, interaction.user.id)
        await interaction.edit_original_response(embed=achievement_embed(states, own=True),
                                                 view=AchievementBoardView(self, interaction.user.id))

    @achievements.command(name="list", description="Посмотреть свои достижения и награды")
    async def list(self, interaction: discord.Interaction) -> None:
        if not await self.user_check(interaction):
            return
        if not await self.bot.database.get_setting(interaction.guild_id, "achievements_enabled", True):
            await interaction.response.send_message(embed=notice("Достижения временно отключены.", kind="warning"),
                                                    ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await self.update_message(interaction)

    @achievements.command(name="view", description="Открытые достижения другого участника")
    async def view(self, interaction: discord.Interaction, user: discord.Member) -> None:
        if not await self.user_check(interaction):
            return
        if not await self.approved(user):
            await interaction.response.send_message(embed=notice("Пользователь ещё не участник NewSquad.",
                                                                  kind="warning"), ephemeral=True)
            return
        profile = await get_profile(self.bot.database, interaction.guild_id, user.id)
        if profile is not None and not profile.public and user.id != interaction.user.id:
            await interaction.response.send_message(embed=notice("Участник скрыл свой профиль.", kind="warning"),
                                                    ephemeral=True)
            return
        await store.sync(self.bot.database, interaction.guild_id, user.id)
        states = await store.list_achievements(self.bot.database, interaction.guild_id, user.id)
        await interaction.response.send_message(embed=achievement_embed(states, own=False), ephemeral=True)

    @achievements.command(name="claim", description="Получить награду за открытое достижение")
    async def claim(self, interaction: discord.Interaction, code: str) -> None:
        if not await self.user_check(interaction):
            return
        if not await self.bot.database.get_setting(interaction.guild_id, "achievements_enabled", True):
            await interaction.response.send_message(embed=notice("Достижения сейчас отключены.", kind="warning"),
                                                    ephemeral=True)
            return
        if not await self.bot.database.get_setting(interaction.guild_id, "economy_enabled", True):
            await interaction.response.send_message(embed=notice("Для получения наград включите экономику.",
                                                                  kind="warning"), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            result = await store.claim(self.bot.database, guild_id=interaction.guild_id,
                                       user_id=interaction.user.id, code=code,
                                       timezone=self.bot.config.timezone)
        except ValueError as exc:
            await interaction.followup.send(embed=notice(str(exc), kind="warning"), ephemeral=True)
            return
        await interaction.followup.send(embed=card("Награда за достижение",
            f"**{store.BY_CODE[code].title}**\n+{result.xp} XP · +{result.coins} NS Coins"), ephemeral=True)

    @achievements.command(name="settings", description="Статус системы достижений")
    async def settings(self, interaction: discord.Interaction) -> None:
        if not await self.admin(interaction):
            return
        enabled = await self.bot.database.get_setting(interaction.guild_id, "achievements_enabled", True)
        await interaction.response.send_message(embed=card("NewSquad  /  Достижения",
            f"**Система:** {'включена' if enabled else 'отключена'}\n"
            f"**Достижений:** {len(store.CATALOG)}\n"
            "**Выдача:** одна награда за каждое достижение, без сброса прогресса.\n"
            "**Профили:** скрытые не показывают условия до открытия."), ephemeral=True)

    @achievements.command(name="switch", description="Включить или отключить достижения")
    async def switch(self, interaction: discord.Interaction, enabled: bool) -> None:
        if not await self.admin(interaction):
            return
        await self.bot.database.set_setting(interaction.guild_id, "achievements_enabled", enabled,
                                            actor_id=interaction.user.id)
        await interaction.response.send_message(embed=notice("Настройки достижений обновлены.", kind="success"),
                                                ephemeral=True)


async def setup(bot) -> None:
    await bot.add_cog(Achievements(bot), guild=discord.Object(id=bot.config.guild_id))
