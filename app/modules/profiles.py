"""Профили одобренных участников: игры, интересы, описание и статус поиска компании."""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from app.services import economy, profiles, progression
from app.services.membership_access import approved_member
from app.ui.theme import card, notice

STATUS = {"available": "Свободен", "looking": "Ищу компанию", "busy": "Не беспокоить"}


class Profiles(commands.Cog):
    profile = app_commands.Group(name="profile", description="Профиль участника NewSquad")

    def __init__(self, bot):
        self.bot = bot

    async def member_ok(self, member: discord.Member) -> bool:
        if member.guild.id != self.bot.config.guild_id or member.bot:
            return False
        role_id = await self.bot.database.get_setting(member.guild.id, "candidate_role_id")
        return await approved_member(self.bot.database, member, role_id)

    async def ensure_owner(self, interaction: discord.Interaction) -> bool:
        if isinstance(interaction.user, discord.Member) and await self.member_ok(interaction.user):
            return True
        await interaction.response.send_message(embed=notice("Профили доступны только участникам NewSquad.",
                                                               kind="warning"), ephemeral=True)
        return False

    @profile.command(name="view", description="Посмотреть свой или чужой публичный профиль")
    async def view(self, interaction: discord.Interaction, member: discord.Member | None = None) -> None:
        target = member or interaction.user
        if not isinstance(target, discord.Member) or not await self.member_ok(target):
            await interaction.response.send_message(embed=notice("Профиль недоступен.", kind="warning"),
                                                    ephemeral=True)
            return
        record = await profiles.get_profile(self.bot.database, interaction.guild_id, target.id)
        if record is not None and not record.public and interaction.user.id != target.id:
            await interaction.response.send_message(embed=notice("Участник закрыл свой профиль.", kind="warning"),
                                                    ephemeral=True)
            return
        exp = await progression.get_progress(self.bot.database, interaction.guild_id, target.id)
        xp = exp.xp if exp else 0
        level = progression.level_for_xp(xp)
        embed = card(f"NewSquad  /  {discord.utils.escape_markdown(target.display_name)}",
            f"**Уровень {level}** · {xp:,} XP\n{progression.progress_bar(xp)}\n"
            f"**Статус:** {STATUS.get(record.availability if record else 'available', 'Свободен')}")
        title = await economy.equipped_title(self.bot.database, interaction.guild_id, target.id)
        if title:
            embed.add_field(name="Титул", value=discord.utils.escape_mentions(
                discord.utils.escape_markdown(title)), inline=False)
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="О себе", value=discord.utils.escape_mentions(record.bio[:500]) if record and record.bio
                        else "Не заполнено", inline=False)
        embed.add_field(name="Игры", value=discord.utils.escape_mentions(record.games) if record and record.games
                        else "Не указаны", inline=True)
        embed.add_field(name="Интересы", value=discord.utils.escape_mentions(record.interests) if record and record.interests
                        else "Не указаны", inline=True)
        embed.add_field(name="Активность", value=(f"Сообщений с XP: {exp.credited_messages if exp else 0}\n"
                        f"Голосовых минут: {exp.active_voice_minutes if exp else 0}"), inline=False)
        if target.joined_at:
            embed.add_field(name="В NewSquad с", value=discord.utils.format_dt(target.joined_at, style="D"))
        await interaction.response.send_message(embed=embed, ephemeral=False,
                                                allowed_mentions=discord.AllowedMentions.none())

    @profile.command(name="edit", description="Изменить описание, любимые игры или интересы")
    @app_commands.choices(field=[
        app_commands.Choice(name="О себе", value="bio"),
        app_commands.Choice(name="Любимые игры", value="games"),
        app_commands.Choice(name="Интересы", value="interests"),
    ])
    async def edit(self, interaction: discord.Interaction, field: app_commands.Choice[str], value: str) -> None:
        if not await self.ensure_owner(interaction):
            return
        try:
            await profiles.update_profile(self.bot.database, interaction.guild_id, interaction.user.id,
                                       field.value, value)
        except ValueError as exc:
            await interaction.response.send_message(embed=notice(str(exc), kind="warning"), ephemeral=True)
            return
        await interaction.response.send_message(embed=notice("Профиль обновлён.", kind="success"), ephemeral=True)

    @profile.command(name="status", description="Изменить статус поиска компании")
    @app_commands.choices(value=[
        app_commands.Choice(name="Свободен", value="available"),
        app_commands.Choice(name="Ищу компанию", value="looking"),
        app_commands.Choice(name="Не беспокоить", value="busy"),
    ])
    async def status(self, interaction: discord.Interaction, value: app_commands.Choice[str]) -> None:
        if not await self.ensure_owner(interaction):
            return
        await profiles.update_profile(self.bot.database, interaction.guild_id, interaction.user.id,
                                   "availability", value.value)
        await interaction.response.send_message(embed=notice("Статус обновлён.", kind="success"), ephemeral=True)

    @profile.command(name="privacy", description="Открыть или скрыть свой профиль от других")
    async def privacy(self, interaction: discord.Interaction, public: bool) -> None:
        if not await self.ensure_owner(interaction):
            return
        await profiles.set_visibility(self.bot.database, interaction.guild_id, interaction.user.id, public)
        await interaction.response.send_message(embed=notice(
            "Ваш профиль публичен." if public else "Ваш профиль теперь виден только вам.", kind="success"),
            ephemeral=True)

    @profile.command(name="reset", description="Очистить персональное оформление без сброса XP")
    async def reset(self, interaction: discord.Interaction) -> None:
        if not await self.ensure_owner(interaction):
            return
        await profiles.reset_profile(self.bot.database, interaction.guild_id, interaction.user.id)
        await interaction.response.send_message(embed=notice("Описание и интересы очищены, XP сохранён.",
                                                               kind="success"), ephemeral=True)


async def setup(bot) -> None:
    await bot.add_cog(Profiles(bot), guild=discord.Object(id=bot.config.guild_id))
