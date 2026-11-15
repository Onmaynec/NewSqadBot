"""Единая привязка всех событий и interactions к одной гильдии."""
from app.config import GUILD_ID


def is_allowed_guild(guild_id: int | None) -> bool:
    return guild_id == GUILD_ID


def is_allowed_interaction(interaction: object) -> bool:
    return is_allowed_guild(getattr(interaction, "guild_id", None))
