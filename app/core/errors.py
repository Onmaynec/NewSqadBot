"""Безопасные сообщения об ошибках взаимодействий Discord."""
from __future__ import annotations

import logging
import discord
from discord import app_commands

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
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            log.warning("Не удалось отправить сообщение об ошибке interaction=%s", interaction.id)

    async def on_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.CheckFailure):
            message = "У вас недостаточно прав для этой команды."
        elif isinstance(error, app_commands.CommandOnCooldown):
            message = "Команда вызывается слишком часто. Попробуйте позже."
        else:
            message = "Не удалось выполнить команду. Администрация может проверить журнал бота."
            log.error("Ошибка команды interaction=%s", interaction.id, exc_info=(type(error), error, error.__traceback__))
        await self._reply(interaction, message)
