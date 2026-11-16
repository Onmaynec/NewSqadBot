"""Guards shared by every role command, panel and deferred operation."""
from __future__ import annotations

from app.config import FEMALE_ROLE_ID

DANGEROUS_PERMISSION_NAMES = (
    "administrator", "manage_guild", "manage_roles", "manage_channels", "manage_webhooks",
    "ban_members", "kick_members", "moderate_members", "manage_messages", "mention_everyone",
    "manage_nicknames", "manage_threads", "view_audit_log",
)


def is_approved(member, candidate_role_id: int | None) -> bool:
    """Участник авторизован, если у него НЕТ роли кандидата.

    Прочие роли, флаг screening и история заявок не влияют на доступ.
    Без настроенной роли кандидата проверка закрыта (fail-closed).
    """
    return (bool(candidate_role_id) and not getattr(member, "bot", False)
            and not any(role.id == candidate_role_id for role in member.roles))


def role_safe_for_self_assign(role, bot_member, forbidden_ids: set[int]) -> bool:
    if role.id in forbidden_ids or role.is_default() or role.managed:
        return False
    if bot_member is None or not bot_member.guild_permissions.manage_roles or bot_member.top_role <= role:
        return False
    return not any(getattr(role.permissions, permission, False) for permission in DANGEROUS_PERMISSION_NAMES)


def forbidden_role_ids(settings: dict[str, int | None]) -> set[int]:
    return {value for value in settings.values() if isinstance(value, int) and value > 0} | {FEMALE_ROLE_ID}


def validate_panel(mode: str, option_count: int) -> None:
    if mode not in {"multi", "single"}:
        raise ValueError("Режим панели: multi или single.")
    if not 1 <= option_count <= 25:
        raise ValueError("Панель должна содержать от 1 до 25 вариантов.")
