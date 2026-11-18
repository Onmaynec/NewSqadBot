"""Human-readable, privacy-conscious presentation of staff audit records.

The SQLite audit facts are unchanged. Names are resolved only from the
current guild cache; missing or deleted Discord entities remain identifiable.
"""
from __future__ import annotations

import json
import re
from typing import Any

ACTIONS = {
    "settings.updated": ("Изменена настройка", "settings"),
    "announcements.published": ("Опубликовано объявление", "communication"),
    "moderation.clear": ("Удалены сообщения", "moderation"),
    "moderation.clearwarns": ("Сняты предупреждения", "moderation"),
    "web.events.created": ("Создано мероприятие", "community"),
    "web.events.cancelled": ("Отменено мероприятие", "community"),
    "web.events.attendance": ("Подтверждено участие", "community"),
    "web.giveaways.created": ("Создан розыгрыш", "community"),
    "web.giveaways.reroll": ("Повторный выбор победителя", "community"),
    "web.suggestions.status_changed": ("Изменён статус предложения", "community"),
    "web.shop.created": ("Добавлен товар", "economy"),
    "web.shop.active": ("Изменена доступность товара", "economy"),
    "web.role_panel.created": ("Создана панель ролей", "roles"),
    "web.role_panel.updated": ("Обновлена панель ролей", "roles"),
    "web.role_panel.deleted": ("Удалена панель ролей", "roles"),
    "web.ticket_category.created": ("Добавлена категория тикетов", "support"),
    "web.ticket_category.updated": ("Обновлена категория тикетов", "support"),
    "web.announcement.created": ("Создано объявление", "communication"),
    "web.announcement.sent": ("Отправлено объявление", "communication"),
    "web.announcement.cancelled": ("Отменено объявление", "communication"),
    "web.role_rule.updated": ("Изменено правило выдачи ролей", "roles"),
}
FIELDS = {
    "target_id": "Объект", "user_id": "Участник", "member_id": "Участник",
    "author_id": "Автор", "actor_id": "Исполнитель", "role_id": "Роль",
    "channel_id": "Канал", "message_id": "Сообщение", "key": "Настройка",
    "source": "Источник", "details": "Описание", "status": "Статус",
    "amount": "Количество", "guild_id": "Сервер", "reason": "Причина",
}
SOURCE_LABELS = {"local_web": "Локальная панель", "web": "Веб-панель",
                 "discord": "Discord", "scheduler": "Планировщик"}
SNOWFLAKE = re.compile(r"(?<![0-9])[0-9]{15,20}(?![0-9])")


def _lookup(guild: Any, identifier: int, kind: str = "") -> str | None:
    if guild is None:
        return None
    if kind in ("role", "any"):
        role = guild.get_role(identifier) if hasattr(guild, "get_role") else None
        if role is not None:
            return f"@{role.name}"
    if kind in ("channel", "any"):
        channel = (guild.get_channel_or_thread(identifier)
                   if hasattr(guild, "get_channel_or_thread") else
                   guild.get_channel(identifier) if hasattr(guild, "get_channel") else None)
        if channel is not None:
            return f"#{channel.name}"
    if kind in ("user", "any"):
        member = guild.get_member(identifier) if hasattr(guild, "get_member") else None
        if member is not None:
            return getattr(member, "display_name", str(member))
    return None


def _kind_for(key: str) -> str:
    if "role" in key:
        return "role"
    if "channel" in key:
        return "channel"
    if any(token in key for token in ("user", "member", "actor", "author", "reviewer")):
        return "user"
    return "any"


def describe_value(guild: Any, value: Any, key: str = "") -> str:
    """Never mistake arbitrary small integer values for Discord IDs."""
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "Да" if value else "Нет"
    if isinstance(value, dict):
        return "; ".join(f"{FIELDS.get(str(k), str(k))}: {describe_value(guild, v, str(k))}"
                         for k, v in list(value.items())[:12])
    if isinstance(value, list):
        return ", ".join(describe_value(guild, v, key) for v in value[:20])
    text = str(value)
    if key == "source":
        return SOURCE_LABELS.get(text, text)
    if key == "key":
        from app.web.catalog import BY_KEY

        spec = BY_KEY.get(text)
        return spec.label if spec is not None else text
    if text.isascii() and text.isdecimal() and 15 <= len(text) <= 20:
        name = _lookup(guild, int(text), _kind_for(key))
        if name:
            return name
        if key in ("target_id", "user_id", "member_id", "author_id", "actor_id",
                   "role_id", "channel_id"):
            return "Недоступный объект · ID " + text
        return text

    # A few legacy events embed IDs inside prose. Resolve only known cached
    # entities, preserving the original ID if the object is unavailable.
    def replace(match: re.Match[str]) -> str:
        name = _lookup(guild, int(match.group()), "any")
        return name if name else match.group()

    return SNOWFLAKE.sub(replace, text)[:1800]


def _title(action: str) -> tuple[str, str]:
    if action in ACTIONS:
        return ACTIONS[action]
    if action.startswith("web."):
        segment = action.split(".")[1]
        categories = {"events": "community", "giveaways": "community",
                      "shop": "economy", "role_panel": "roles", "role_rule": "roles",
                      "announcement": "communication", "ticket_category": "support"}
        return action.replace("web.", "").replace(".", " / ").replace("_", " ").capitalize(), categories.get(segment, "system")
    if action.startswith("moderation."):
        return action.replace(".", " / ").replace("_", " ").capitalize(), "moderation"
    return action.replace(".", " / ").replace("_", " ").capitalize(), "system"


def present_audit(row: Any, guild: Any) -> dict:
    """Keep raw fields for compatibility and add safe, localized display data."""
    try:
        data = json.loads(row.data_json)
    except (ValueError, TypeError):
        data = {}
    if not isinstance(data, dict):
        data = {"details": data}
    title, category = _title(row.action)
    actor = _lookup(guild, row.actor_id, "user") if row.actor_id else None
    actor_label = actor or ("Локальная панель" if not row.actor_id and
                            data.get("source") == "local_web" else
                            "Система" if not row.actor_id else
                            "Участник недоступен · ID " + str(row.actor_id))
    fields = []
    for key, value in data.items():
        if value is None or value == "":
            continue
        fields.append({"key": key, "label": FIELDS.get(key, key.replace("_", " ").capitalize()),
                       "value": describe_value(guild, value, key)})
    primary = next((field["value"] for field in fields if field["key"] == "details"), "")
    if not primary:
        primary = next((field["value"] for field in fields if field["key"] == "key"), "")
    if not primary:
        primary = next((field["value"] for field in fields if field["key"] == "target_id"), "")
    return {
        "id": row.id, "actor_id": str(row.actor_id), "actor_name": actor_label,
        "action": row.action, "title": title, "category": category,
        "summary": primary[:240], "fields": fields, "details": data,
        "at": row.created_at,
    }
