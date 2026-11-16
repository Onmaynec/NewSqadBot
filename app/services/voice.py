"""Записи голосовых комнат: одна активная комната на владельца, сохраняется при перезапуске."""
from __future__ import annotations

import time

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError

from app.models import VoiceRoom, VoiceRoomAccess


async def by_owner(db, guild_id: int, owner_id: int) -> VoiceRoom | None:
    async with db.sessions() as session:
        return await session.scalar(select(VoiceRoom).where(
            VoiceRoom.guild_id == guild_id, VoiceRoom.owner_id == owner_id))


async def by_channel(db, guild_id: int, channel_id: int) -> VoiceRoom | None:
    async with db.sessions() as session:
        return await session.scalar(select(VoiceRoom).where(
            VoiceRoom.guild_id == guild_id, VoiceRoom.channel_id == channel_id))


async def all_rooms(db, guild_id: int) -> list[VoiceRoom]:
    async with db.sessions() as session:
        return list((await session.scalars(select(VoiceRoom).where(
            VoiceRoom.guild_id == guild_id).order_by(VoiceRoom.created_at))).all())


async def register(db, *, guild_id: int, channel_id: int, owner_id: int, lobby_id: int,
                   name: str, user_limit: int = 0) -> VoiceRoom:
    if not 0 <= user_limit <= 99:
        raise ValueError("Лимит участников должен быть от 0 до 99.")
    try:
        async with db.session() as session:
            row = VoiceRoom(guild_id=guild_id, channel_id=channel_id, owner_id=owner_id,
                lobby_id=lobby_id, name=name[:100], user_limit=user_limit,
                locked=False, hidden=False, created_at=int(time.time()), updated_at=int(time.time()))
            session.add(row)
            await session.flush()
            return row
    except IntegrityError as exc:
        raise ValueError("Участник уже владеет комнатой или канал зарегистрирован.") from exc


async def rename(db, guild_id: int, channel_id: int, *, name: str) -> None:
    if not 1 <= len(name.strip()) <= 100:
        raise ValueError("Название комнаты должно содержать от 1 до 100 символов.")
    async with db.session() as session:
        await session.execute(update(VoiceRoom).where(VoiceRoom.guild_id == guild_id,
            VoiceRoom.channel_id == channel_id).values(name=name.strip(), updated_at=int(time.time())))


async def update_state(db, guild_id: int, channel_id: int, *, locked: bool | None = None,
                       hidden: bool | None = None, user_limit: int | None = None) -> None:
    values: dict = {"updated_at": int(time.time())}
    if locked is not None:
        values["locked"] = locked
    if hidden is not None:
        values["hidden"] = hidden
    if user_limit is not None:
        if not 0 <= user_limit <= 99:
            raise ValueError("Лимит участников должен быть от 0 до 99.")
        values["user_limit"] = user_limit
    async with db.session() as session:
        await session.execute(update(VoiceRoom).where(
            VoiceRoom.guild_id == guild_id, VoiceRoom.channel_id == channel_id).values(**values))


async def transfer(db, guild_id: int, channel_id: int, new_owner_id: int) -> None:
    try:
        async with db.session() as session:
            result = await session.execute(update(VoiceRoom).where(
                VoiceRoom.guild_id == guild_id, VoiceRoom.channel_id == channel_id).values(
                owner_id=new_owner_id, updated_at=int(time.time())))
            if result.rowcount != 1:
                raise ValueError("Комната не найдена.")
    except IntegrityError as exc:
        raise ValueError("У выбранного участника уже есть собственная комната.") from exc


async def remove(db, guild_id: int, channel_id: int) -> None:
    async with db.session() as session:
        room = await session.scalar(select(VoiceRoom).where(
            VoiceRoom.guild_id == guild_id, VoiceRoom.channel_id == channel_id))
        if room is None:
            return
        await session.execute(delete(VoiceRoomAccess).where(VoiceRoomAccess.room_id == room.id))
        await session.delete(room)


async def access_entries(db, room_id: int) -> list[VoiceRoomAccess]:
    async with db.sessions() as session:
        return list((await session.scalars(select(VoiceRoomAccess).where(
            VoiceRoomAccess.room_id == room_id))).all())


async def access_mode(db, room_id: int, user_id: int) -> str | None:
    async with db.sessions() as session:
        return await session.scalar(select(VoiceRoomAccess.mode).where(
            VoiceRoomAccess.room_id == room_id, VoiceRoomAccess.user_id == user_id))


async def set_access(db, room_id: int, user_id: int, mode: str) -> None:
    if mode not in {"allow", "deny"}:
        raise ValueError("Режим доступа: allow или deny.")
    async with db.session() as session:
        await session.execute(sqlite_insert(VoiceRoomAccess).values(
            room_id=room_id, user_id=user_id, mode=mode).on_conflict_do_update(
            index_elements=[VoiceRoomAccess.room_id, VoiceRoomAccess.user_id], set_={"mode": mode}))


async def clear_access(db, room_id: int, user_id: int) -> None:
    async with db.session() as session:
        await session.execute(delete(VoiceRoomAccess).where(
            VoiceRoomAccess.room_id == room_id, VoiceRoomAccess.user_id == user_id))
