"""Проверка прав на стороне сервера, а не только в интерфейсе."""
from app.core.guild_guard import is_allowed_interaction


def can_manage_settings(interaction: object, owner_ids: frozenset[int]) -> bool:
    if not is_allowed_interaction(interaction):
        return False
    guild = getattr(interaction, "guild", None)
    user = getattr(interaction, "user", None)
    if guild is None or user is None:
        return False
    user_id = getattr(user, "id", None)
    if user_id == getattr(guild, "owner_id", None) or user_id in owner_ids:
        return True
    permissions = getattr(user, "guild_permissions", None)
    return bool(permissions and permissions.administrator)
