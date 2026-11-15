"""Базовый Discord-аудит (личные тексты сообщений не записываются)."""
from __future__ import annotations

import discord
from discord.ext import commands

from app.services.audit import log_event


class AuditLogs(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def allowed(self, guild_id: int) -> bool:
        return guild_id == self.bot.config.guild_id

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if self.allowed(member.guild.id):
            await log_event(self.bot, channel_setting="audit_channel_id", title="Участник зашёл",
                            details=f"ID: {member.id}", action="member.join", target_id=member.id)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        if self.allowed(member.guild.id):
            await log_event(self.bot, channel_setting="audit_channel_id", title="Участник покинул сервер",
                            details=f"ID: {member.id}", action="member.leave", target_id=member.id)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        if not self.allowed(after.guild.id):
            return
        removed = {r.id for r in before.roles} - {r.id for r in after.roles}
        added = {r.id for r in after.roles} - {r.id for r in before.roles}
        if added or removed:
            await log_event(self.bot, channel_setting="audit_channel_id", title="Изменены роли участника",
                            details=f"ID: {after.id}; добавлены: {sorted(added)}; сняты: {sorted(removed)}",
                            action="member.roles", target_id=after.id)

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        if self.allowed(channel.guild.id):
            await log_event(self.bot, channel_setting="audit_channel_id", title="Создан канал",
                            details=f"Канал: {channel.id}", action="channel.create", target_id=channel.id)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        if self.allowed(channel.guild.id):
            await log_event(self.bot, channel_setting="audit_channel_id", title="Удалён канал",
                            details=f"Канал: {channel.id}", action="channel.delete", target_id=channel.id)


async def setup(bot):
    await bot.add_cog(AuditLogs(bot), guild=discord.Object(id=bot.config.guild_id))
