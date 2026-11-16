"""Безопасные сообщения об ошибках взаимодействий Discord."""
from __future__ import annotations

import logging

import discord
from discord import app_commands

from app.ui.theme import notice

log = logging.getLogger(__name__)


class GuildCommandTree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        from app.core.guild_guard import is_allowed_interaction
        allowed = is_allowed_interaction(interaction)
        if not allowed:
            await self._reply(interaction, "NewSqadBot работает только на сервере NewSquad.")
        return allowed

    @staticmethod
    async def _reply(interaction: discord.Interaction, message: str) -> None:
        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=notice(message, kind="error"), ephemeral=True)
            else:
                await interaction.response.send_message(embed=notice(message, kind="error"), ephemeral=True)
        except discord.HTTPException:
            log.warning("Не удалось отправить сообщение об ошибке interaction=%s", interaction.id)

    async def on_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.CheckFailure):
            message = "У вас недостаточно прав для этой команды."
        elif isinstance(error, app_commands.CommandOnCooldown):
            message = "Команда вызывается слишком часто. Попробуйте позже."
        else:
            message = "Не удалось выполнить команду. Администрация может проверить журнал бота."
            command_name = interaction.command.qualified_name if interaction.command else "неизвестная команда"
            log.error("Ошибка команды %s: guild_id=%s user_id=%s interaction_id=%s type=%s",
                      command_name, interaction.guild_id, interaction.user.id, interaction.id,
                      type(error).__name__, exc_info=(type(error), error, error.__traceback__))
        await self._reply(interaction, message)
