"""Транзакционное хранилище объявлений и шаблонов (SQLite).

Отправка в Discord отделена от БД. После перехода в sending запрос никогда
не повторяется автоматически: при обрыве связи состояние станет uncertain,
чтобы не публиковать дубликат.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.models import Announcement, AnnouncementTemplate

SLUG_RE = re.compile(r"[a-z0-9][a-z0-9_-]{1,31}\Z")
STATUS_LABELS = {
    "draft": "Черновик",
    "scheduled": "Запланировано",
    "sending": "Отправляется",
    "sent": "Опубликовано",
    "uncertain": "Требует проверки",
    "failed": "Ошибка отправки",
    "cancelled": "Отменено",
}


def validate(kind: str, title: str, body: str, color: int) -> tuple[str, str]:
    title, body = title.strip(), body.strip()
    if kind not in ("embed", "text"):
        raise ValueError("Формат должен быть text или embed.")
    if not 1 <= len(title) <= 120 or "\r" in title or "\n" in title:
        raise ValueError("Заголовок: одна строка от 1 до 120 символов.")
    limit = 3500 if kind == "embed" else 1750
    if not 1 <= len(body) <= limit:
        raise ValueError(f"Описание: от 1 до {limit} символов.")
    if color < 0 or color > 0xFFFFFF:
        raise ValueError("Цвет Embed должен быть RGB-числом.")
    return title, body


def parse_time(value: str, *, timezone_name: str, now: int | None = None) -> int:
    """Дата вида ГГГГ-ММ-ДД ЧЧ:ММ в TZ бота, с проверкой перехода DST."""
    try:
        naïve = datetime.strptime(value.strip(), "%Y-%m-%d %H:%M")
        zone = ZoneInfo(timezone_name)
    except (ValueError, KeyError) as exc:
        raise ValueError("Введите дату и время: ГГГГ-ММ-ДД ЧЧ:ММ.") from exc
    first = naïve.replace(tzinfo=zone, fold=0)
    second = naïve.replace(tzinfo=zone, fold=1)
    if first.utcoffset() != second.utcoffset():
        raise ValueError("Время попадает на перевод часов. Выберите другое время.")
    timestamp = int(first.timestamp())
    if datetime.fromtimestamp(timestamp, zone).replace(tzinfo=None) != naïve:
        raise ValueError("Такого местного времени не существует.")
    now = int(time.time()) if now is None else now
    if not now + 60 <= timestamp <= now + 365 * 86400:
        raise ValueError("Публикация: не раньше чем через минуту и не позже чем через год.")
    return timestamp


async def create(db, *, guild_id: int, author_id: int, channel_id: int,
                 kind: str, title: str, body: str, color: int, now: int | None = None) -> Announcement:
    title, body = validate(kind, title, body, color)
    now = int(time.time()) if now is None else now
    async with db.session() as session:
        row = Announcement(
            guild_id=guild_id, author_id=author_id, channel_id=channel_id,
            kind=kind, title=title, body=body, color=color, status="draft",
            due_at=None, message_id=None, attempts=0, error=None,
            created_at=now, updated_at=now,
        )
        session.add(row)
        await session.flush()
        number = row.id
    return await get(db, guild_id, number)


async def get(db, guild_id: int, number: int) -> Announcement | None:
    async with db.sessions() as session:
        return await session.scalar(select(Announcement).where(
            Announcement.guild_id == guild_id, Announcement.id == number,
        ))


async def list_items(db, guild_id: int, *, statuses: tuple[str, ...] = ("draft",),
                     limit: int = 15) -> list[Announcement]:
    async with db.sessions() as session:
        rows = await session.scalars(select(Announcement).where(
            Announcement.guild_id == guild_id, Announcement.status.in_(statuses),
        ).order_by(Announcement.id.desc()).limit(limit))
        return list(rows.all())


async def edit(db, *, guild_id: int, number: int, actor_id: int,
               title: str, body: str, now: int | None = None) -> Announcement:
    now = int(time.time()) if now is None else now
    async with db.session() as session:
        row = await session.scalar(select(Announcement).where(
            Announcement.id == number, Announcement.guild_id == guild_id,
        ))
        if row is None or row.status not in ("draft", "failed"):
            raise ValueError("Изменять можно только черновики и неотправленные записи с ошибкой.")
        title, body = validate(row.kind, title, body, row.color)
        row.title, row.body, row.updated_at, row.status = title, body, now, "draft"
        row.error = None
    return await get(db, guild_id, number)


async def schedule(db, *, guild_id: int, number: int,
                   due_at: int, now: int | None = None) -> Announcement:
    now = int(time.time()) if now is None else now
    if not now + 60 <= due_at <= now + 365 * 86400:
        raise ValueError("Время публикации должно быть от минуты до одного года в будущем.")
    async with db.session() as session:
        result = await session.execute(update(Announcement).where(
            Announcement.id == number, Announcement.guild_id == guild_id,
            Announcement.status == "draft",
        ).values(status="scheduled", due_at=due_at, updated_at=now))
        if result.rowcount != 1:
            raise ValueError("Публикацию можно планировать только из черновика.")
    return await get(db, guild_id, number)


async def cancel(db, *, guild_id: int, number: int,
                 now: int | None = None) -> Announcement:
    now = int(time.time()) if now is None else now
    async with db.session() as session:
        result = await session.execute(update(Announcement).where(
            Announcement.id == number, Announcement.guild_id == guild_id,
            Announcement.status.in_(("draft", "scheduled", "failed")),
        ).values(status="cancelled", updated_at=now))
        if result.rowcount != 1:
            raise ValueError("Нельзя отменить уже отправленное или отправляемое объявление.")
    return await get(db, guild_id, number)


async def claim(db, *, guild_id: int, number: int,
                scheduled_only: bool, now: int | None = None) -> Announcement | None:
    now = int(time.time()) if now is None else now
    async with db.session() as session:
        query = update(Announcement).where(
            Announcement.id == number, Announcement.guild_id == guild_id,
        )
        if scheduled_only:
            query = query.where(
                Announcement.status == "scheduled", Announcement.due_at <= now,
            )
        else:
            query = query.where(Announcement.status == "draft")
        result = await session.execute(query.values(
            status="sending", attempts=Announcement.attempts + 1,
            updated_at=now, error=None,
        ))
        if result.rowcount != 1:
            return None
    return await get(db, guild_id, number)


async def mark_sent(db, *, guild_id: int, number: int, message_id: int,
                    now: int | None = None) -> None:
    now = int(time.time()) if now is None else now
    async with db.session() as session:
        result = await session.execute(update(Announcement).where(
            Announcement.id == number, Announcement.guild_id == guild_id,
            Announcement.status == "sending",
        ).values(status="sent", message_id=message_id, error=None, updated_at=now))
        if result.rowcount != 1:
            raise RuntimeError(f"Не удалось записать результат публикации {number}")


async def mark_error(db, *, guild_id: int, number: int, uncertain: bool,
                     detail: str, now: int | None = None) -> None:
    now = int(time.time()) if now is None else now
    async with db.session() as session:
        await session.execute(update(Announcement).where(
            Announcement.id == number, Announcement.guild_id == guild_id,
            Announcement.status == "sending",
        ).values(
            status="uncertain" if uncertain else "failed",
            error=detail[:300], updated_at=now,
        ))


async def recover_sending(db, guild_id: int) -> int:
    """После рестарта не повторять запрос с неизвестным результатом."""
    async with db.session() as session:
        result = await session.execute(update(Announcement).where(
            Announcement.guild_id == guild_id, Announcement.status == "sending",
        ).values(status="uncertain", error="Сбой или перезапуск во время отправки. Проверьте канал."))
        return result.rowcount


async def duplicate(db, *, guild_id: int, number: int, author_id: int) -> Announcement:
    original = await get(db, guild_id, number)
    if original is None:
        raise ValueError("Объявление не найдено.")
    return await create(
        db, guild_id=guild_id, author_id=author_id, channel_id=original.channel_id,
        kind=original.kind, title=original.title, body=original.body, color=original.color,
    )


async def save_template(db, *, guild_id: int, slug: str, author_id: int,
                        number: int, now: int | None = None) -> None:
    slug = slug.lower().strip()
    if not SLUG_RE.fullmatch(slug):
        raise ValueError("Название шаблона: 2–32 символа, a-z, цифры, _ или -.")
    row = await get(db, guild_id, number)
    if row is None:
        raise ValueError("Объявление не найдено.")
    now = int(time.time()) if now is None else now
    async with db.session() as session:
        await session.execute(sqlite_insert(AnnouncementTemplate).values(
            guild_id=guild_id, slug=slug, owner_id=author_id, kind=row.kind,
            title=row.title, body=row.body, color=row.color, updated_at=now,
        ).on_conflict_do_update(
            index_elements=[AnnouncementTemplate.guild_id, AnnouncementTemplate.slug],
            set_={
                "owner_id": author_id, "kind": row.kind, "title": row.title,
                "body": row.body, "color": row.color, "updated_at": now,
            },
        ))


async def templates(db, guild_id: int) -> list[AnnouncementTemplate]:
    async with db.sessions() as session:
        rows = await session.scalars(select(AnnouncementTemplate).where(
            AnnouncementTemplate.guild_id == guild_id,
        ).order_by(AnnouncementTemplate.slug).limit(50))
        return list(rows.all())


async def from_template(db, *, guild_id: int, slug: str,
                        author_id: int, channel_id: int) -> Announcement:
    async with db.sessions() as session:
        template = await session.scalar(select(AnnouncementTemplate).where(
            AnnouncementTemplate.guild_id == guild_id,
            AnnouncementTemplate.slug == slug.strip().lower(),
        ))
        if template is None:
            raise ValueError("Шаблон не найден.")
        values = (template.kind, template.title, template.body, template.color)
    return await create(
        db, guild_id=guild_id, author_id=author_id, channel_id=channel_id,
        kind=values[0], title=values[1], body=values[2], color=values[3],
    )
