"""Транзакционная логика Party Finder. Одна запись состава — одно место в группе."""
from __future__ import annotations

import time

from sqlalchemy import case, delete, select, update
from sqlalchemy.exc import IntegrityError

from app.models import PartyListing, PartyMember

ACTIVE = ("open", "full")


async def get(db, guild_id: int, party_id: int) -> PartyListing | None:
    async with db.sessions() as session:
        return await session.scalar(select(PartyListing).where(
            PartyListing.guild_id == guild_id, PartyListing.id == party_id))


async def members(db, party_id: int) -> list[int]:
    async with db.sessions() as session:
        return list((await session.scalars(select(PartyMember.user_id).where(
            PartyMember.party_id == party_id).order_by(PartyMember.joined_at, PartyMember.id)))).all())


async def list_active(db, guild_id: int, *, game: str | None = None, limit: int = 20) -> list[PartyListing]:
    now = int(time.time())
    async with db.sessions() as session:
        q = select(PartyListing).where(
            PartyListing.guild_id == guild_id, PartyListing.status.in_(ACTIVE),
            PartyListing.expires_at > now)
        if game:
            q = q.where(PartyListing.game.ilike(f"%{game.strip()}%"))
        return list((await session.scalars(q.order_by(PartyListing.created_at.desc()).limit(limit))).all())


async def create(db, *, guild_id: int, owner_id: int, game: str, description: str,
                 capacity: int, expires_at: int, starts_at: int | None = None,
                 channel_id: int | None = None, voice_requested: bool = False) -> PartyListing:
    now = int(time.time())
    game, description = game.strip(), description.strip()
    if not 2 <= capacity <= 25 or not 1 <= len(game) <= 80 or not 1 <= len(description) <= 500:
        raise ValueError("Игра, описание или размер группы указаны неверно.")
    if not now + 60 <= expires_at <= now + 7 * 86400:
        raise ValueError("Время жизни объявления: от минуты до семи дней.")
    if starts_at is not None and not now <= starts_at < expires_at:
        raise ValueError("Время начала должно быть раньше окончания поиска.")
    await expire_due(db, guild_id, now=now)
    try:
        async with db.session() as session:
            row = PartyListing(guild_id=guild_id, owner_id=owner_id, game=game, description=description,
                capacity=capacity, member_count=1, status="open", starts_at=starts_at, expires_at=expires_at,
                channel_id=channel_id, voice_requested=voice_requested, created_at=now, updated_at=now)
            session.add(row)
            await session.flush()
            session.add(PartyMember(party_id=row.id, user_id=owner_id, joined_at=now))
            party_id = row.id
    except IntegrityError as exc:
        raise ValueError("У вас уже есть активное объявление. Закройте его перед созданием нового.") from exc
    return await get(db, guild_id, party_id)


async def join(db, guild_id: int, party_id: int, user_id: int) -> PartyListing:
    now = int(time.time())
    try:
        async with db.session() as session:
            listing = await session.scalar(select(PartyListing).where(
                PartyListing.id == party_id, PartyListing.guild_id == guild_id))
            if listing is None or listing.status not in ACTIVE or listing.expires_at <= now:
                raise ValueError("Объявление закрыто или срок поиска истёк.")
            exists = await session.scalar(select(PartyMember.id).where(
                PartyMember.party_id == party_id, PartyMember.user_id == user_id))
            if exists is not None:
                raise ValueError("Вы уже состоите в этой группе.")
            result = await session.execute(update(PartyListing).where(
                PartyListing.id == party_id, PartyListing.guild_id == guild_id,
                PartyListing.status == "open", PartyListing.expires_at > now,
                PartyListing.member_count < PartyListing.capacity
            ).values(
                member_count=PartyListing.member_count + 1,
                status=case((PartyListing.member_count + 1 >= PartyListing.capacity, "full"), else_="open"),
                updated_at=now,
            ))
            if result.rowcount != 1:
                raise ValueError("Свободных мест больше нет или объявление закрыто.")
            session.add(PartyMember(party_id=party_id, user_id=user_id, joined_at=now))
    except IntegrityError as exc:
        raise ValueError("Вы уже состоите в группе.") from exc
    return await get(db, guild_id, party_id)


async def leave(db, guild_id: int, party_id: int, user_id: int) -> PartyListing:
    now = int(time.time())
    async with db.session() as session:
        row = await session.scalar(select(PartyListing).where(
            PartyListing.id == party_id, PartyListing.guild_id == guild_id))
        if row is None or row.status not in ACTIVE or row.expires_at <= now:
            raise ValueError("Группа уже закрыта.")
        if row.owner_id == user_id:
            raise ValueError("Автор может закрыть объявление командой /party close.")
        member = await session.scalar(select(PartyMember).where(
            PartyMember.party_id == party_id, PartyMember.user_id == user_id))
        if member is None:
            raise ValueError("Вас нет в составе этой группы.")
        result = await session.execute(update(PartyListing).where(
            PartyListing.id == party_id, PartyListing.guild_id == guild_id,
            PartyListing.status.in_(ACTIVE), PartyListing.member_count > 1,
        ).values(member_count=PartyListing.member_count - 1, status="open", updated_at=now))
        if result.rowcount != 1:
            raise ValueError("Состав группы изменился. Попробуйте ещё раз.")
        await session.delete(member)
    return await get(db, guild_id, party_id)


async def close(db, guild_id: int, party_id: int, actor_id: int, *, admin: bool = False) -> PartyListing:
    now = int(time.time())
    async with db.session() as session:
        row = await session.scalar(select(PartyListing).where(
            PartyListing.id == party_id, PartyListing.guild_id == guild_id))
        if row is None:
            raise ValueError("Объявление не найдено.")
        if row.owner_id != actor_id and not admin:
            raise ValueError("Закрыть объявление может автор или администратор.")
        result = await session.execute(update(PartyListing).where(
            PartyListing.id == party_id, PartyListing.status.in_(ACTIVE)
        ).values(status="closed", updated_at=now))
        if result.rowcount != 1:
            raise ValueError("Объявление уже закрыто.")
    return await get(db, guild_id, party_id)


async def expire_due(db, guild_id: int, *, now: int | None = None) -> list[int]:
    now = int(time.time()) if now is None else now
    async with db.session() as session:
        ids = list((await session.scalars(select(PartyListing.id).where(
            PartyListing.guild_id == guild_id, PartyListing.status.in_(ACTIVE),
            PartyListing.expires_at <= now)))).all())
        if ids:
            await session.execute(update(PartyListing).where(
                PartyListing.id.in_(ids), PartyListing.status.in_(ACTIVE),
                PartyListing.expires_at <= now).values(status="expired", updated_at=now))
    return ids


async def set_message(db, guild_id: int, party_id: int, message_id: int) -> None:
    async with db.session() as session:
        await session.execute(update(PartyListing).where(
            PartyListing.guild_id == guild_id, PartyListing.id == party_id,
            PartyListing.message_id.is_(None)).values(message_id=message_id))


async def set_voice(db, guild_id: int, party_id: int, channel_id: int) -> None:
    async with db.session() as session:
        await session.execute(update(PartyListing).where(
            PartyListing.guild_id == guild_id, PartyListing.id == party_id,
            PartyListing.status.in_(ACTIVE)).values(voice_channel_id=channel_id))
