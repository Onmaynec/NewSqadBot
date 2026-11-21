"""Voice 2.0 owner presets and aggregate-only usage (no member/voice recordings)."""
from __future__ import annotations

import time
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.models import VoiceOwnerPreference, VoiceUsageDay

PRESETS = {
    "public": {"locked": False, "hidden": False, "limit": 0},
    "private": {"locked": True, "hidden": True, "limit": 0},
    "team": {"locked": True, "hidden": False, "limit": 10},
}


async def preference(db, guild_id: int, owner_id: int) -> VoiceOwnerPreference | None:
    async with db.sessions() as session:
        return await session.scalar(select(VoiceOwnerPreference).where(
            VoiceOwnerPreference.guild_id == guild_id, VoiceOwnerPreference.owner_id == owner_id))


async def save(db, guild_id: int, owner_id: int, *, template: str,
               name: str = "", limit: int | None = None,
               locked: bool | None = None, hidden: bool | None = None) -> None:
    if template not in PRESETS:
        raise ValueError("Выберите public, private или team.")
    if len(name) > 90 or any(ord(ch) < 32 for ch in name):
        raise ValueError("Некорректное имя комнаты.")
    values = PRESETS[template]
    limit = values["limit"] if limit is None else limit
    locked = values["locked"] if locked is None else locked
    hidden = values["hidden"] if hidden is None else hidden
    if not 0 <= limit <= 99 or type(locked) is not bool or type(hidden) is not bool:
        raise ValueError("Недопустимые параметры комнаты.")
    now = int(time.time())
    async with db.session() as session:
        stmt = sqlite_insert(VoiceOwnerPreference).values(
            guild_id=guild_id, owner_id=owner_id, template=template,
            room_name=name.strip(), user_limit=limit,
            locked=locked, hidden=hidden, updated_at=now)
        await session.execute(stmt.on_conflict_do_update(
            index_elements=["guild_id", "owner_id"],
            set_={"template": template, "room_name": name.strip(), "user_limit": limit,
                  "locked": locked, "hidden": hidden, "updated_at": now}))


async def usage(db, guild_id: int, *, days: int = 30) -> list[dict]:
    if days not in (7, 14, 30, 90):
        raise ValueError("Период должен быть 7/14/30/90.")
    cutoff = datetime.fromtimestamp(time.time() - days * 86400, timezone.utc).date().isoformat()
    async with db.sessions() as session:
        rows = (await session.scalars(select(VoiceUsageDay).where(
            VoiceUsageDay.guild_id == guild_id, VoiceUsageDay.day_utc >= cutoff)
            .order_by(VoiceUsageDay.day_utc).limit(days + 2))).all()
        return [{"day": row.day_utc, "rooms_created": row.rooms_created,
                 "occupied_seconds": row.occupied_seconds} for row in rows]
