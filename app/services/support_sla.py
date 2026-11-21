"""Support priority and per-priority SLA policy shared by Discord and web API.

Independent from existing auto-close inactivity rules: SLA triggers alerts, not
destructive ticket deletion. No message bodies or transcript content is stored.
"""
from __future__ import annotations

import time

from sqlalchemy import select, update

from app.models import Ticket
from app.services.tickets import event

PRIORITIES = ("low", "normal", "high", "urgent")
DEFAULT_RULES = {
    "low": {"first_minutes": 1440, "idle_minutes": 4320},
    "normal": {"first_minutes": 360, "idle_minutes": 1440},
    "high": {"first_minutes": 120, "idle_minutes": 480},
    "urgent": {"first_minutes": 30, "idle_minutes": 120},
}


def validate_rules(value: dict) -> dict:
    if not isinstance(value, dict) or set(value) != set(PRIORITIES):
        raise ValueError("Требуются правила low, normal, high, urgent.")
    result = {}
    for key in PRIORITIES:
        rule = value[key]
        if not isinstance(rule, dict) or set(rule) != {"first_minutes", "idle_minutes"}:
            raise ValueError("Для каждого приоритета укажите first_minutes и idle_minutes.")
        if any(type(rule[k]) is not int or not 5 <= rule[k] <= 43200 for k in rule):
            raise ValueError("SLA: от 5 минут до 30 дней.")
        result[key] = dict(rule)
    return result


async def get_rules(db, guild_id: int) -> dict:
    stored = await db.get_setting(guild_id, "tickets_sla_rules", None)
    if stored is None:
        return {key: dict(values) for key, values in DEFAULT_RULES.items()}
    return validate_rules(stored)


async def save_rules(db, guild_id: int, value: dict, *, actor_id: int) -> dict:
    rules = validate_rules(value)
    await db.set_setting(guild_id, "tickets_sla_rules", rules, actor_id=actor_id)
    return rules


async def change_priority(db, guild_id: int, ticket_id: int, value: str,
                          actor_id: int) -> bool:
    if value not in PRIORITIES or actor_id <= 0:
        raise ValueError("Неизвестный приоритет или сотрудник.")
    async with db.session() as session:
        row = await session.scalar(select(Ticket).where(
            Ticket.id == ticket_id, Ticket.guild_id == guild_id,
            Ticket.status == "open"))
        if row is None:
            return False
        if row.priority == value:
            return True
        changed = await session.execute(update(Ticket).where(
            Ticket.id == ticket_id, Ticket.guild_id == guild_id,
            Ticket.status == "open", Ticket.priority == row.priority
        ).values(priority=value))
        if changed.rowcount != 1:
            return False
        event(session, ticket_id, actor_id, "priority_changed",
              previous=row.priority, priority=value)
    return True


def indicators(ticket, rules: dict, *, now: int | None = None) -> dict:
    now = int(time.time()) if now is None else now
    policy = rules.get(ticket.priority, rules["normal"])
    first_due = ticket.opened_at + policy["first_minutes"] * 60
    idle_due = ticket.updated_at + policy["idle_minutes"] * 60
    return {
        "priority": ticket.priority,
        "first_due_at": first_due if ticket.first_response_at is None else None,
        "first_breached": bool(ticket.first_response_at is None and now >= first_due),
        "idle_due_at": idle_due,
        "idle_breached": now >= idle_due,
    }
