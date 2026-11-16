"""Редактируемые пользователем поля профиля; не публикует содержимое анкеты Membership."""
from __future__ import annotations

import time

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.models import MemberProfile

FIELDS = {"bio": 500, "games": 250, "interests": 250, "availability": 16}
AVAILABILITY = {"available", "looking", "busy"}


def clean(field: str, value: str) -> str:
    if field not in FIELDS:
        raise ValueError("Неизвестное поле профиля")
    value = " ".join(value.strip().split())
    if len(value) > FIELDS[field] or any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValueError(f"Поле должно содержать не более {FIELDS[field]} символов")
    if field == "availability" and value not in AVAILABILITY:
        raise ValueError("Статус: available, looking или busy")
    return value


async def get_profile(db, guild_id: int, user_id: int) -> MemberProfile | None:
    async with db.sessions() as session:
        return await session.scalar(select(MemberProfile).where(
            MemberProfile.guild_id == guild_id, MemberProfile.user_id == user_id))


async def update_profile(db, guild_id: int, user_id: int, field: str, value: str) -> None:
    value = clean(field, value)
    now = int(time.time())
    async with db.session() as session:
        await session.execute(sqlite_insert(MemberProfile).values(
            guild_id=guild_id, user_id=user_id, bio="", games="", interests="",
            availability="available", public=True, updated_at=now,
        ).on_conflict_do_nothing(index_elements=[MemberProfile.guild_id, MemberProfile.user_id]))
        row = await session.scalar(select(MemberProfile).where(
            MemberProfile.guild_id == guild_id, MemberProfile.user_id == user_id))
        setattr(row, field, value)
        row.updated_at = now


async def set_visibility(db, guild_id: int, user_id: int, public: bool) -> None:
    now = int(time.time())
    async with db.session() as session:
        await session.execute(sqlite_insert(MemberProfile).values(
            guild_id=guild_id, user_id=user_id, bio="", games="", interests="",
            availability="available", public=public, updated_at=now,
        ).on_conflict_do_nothing(index_elements=[MemberProfile.guild_id, MemberProfile.user_id]))
        row = await session.scalar(select(MemberProfile).where(
            MemberProfile.guild_id == guild_id, MemberProfile.user_id == user_id))
        row.public = public
        row.updated_at = now


async def reset_profile(db, guild_id: int, user_id: int) -> None:
    async with db.session() as session:
        await session.execute(delete(MemberProfile).where(
            MemberProfile.guild_id == guild_id, MemberProfile.user_id == user_id))
