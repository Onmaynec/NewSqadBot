"""Транзакционный сервис предложений: голосование, статус и антиспам."""
from __future__ import annotations

import json
import time
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.models import (
    OutboxEvent,
    Suggestion,
    SuggestionCreatePanel,
    SuggestionStatusEvent,
    SuggestionThrottle,
    SuggestionVote,
)

STATES = ("new", "under_review", "accepted", "rejected", "implemented")
OPEN_STATES = ("new", "under_review")


def clean(raw: str, *, minimum: int = 1, maximum: int = 500, multiline: bool = False) -> str:
    if not isinstance(raw, str) or any(
        ord(char) < 32 and char not in ("\n", "\r", "\t") or ord(char) == 127
        for char in raw
    ):
        raise ValueError("Текст содержит недопустимые символы.")
    lines = raw.splitlines() if multiline else [raw]
    value = "\n".join(" ".join(line.split()) for line in lines).strip()
    if not minimum <= len(value) <= maximum:
        raise ValueError(f"Длина текста должна быть от {minimum} до {maximum} символов.")
    return value


async def create(db, *, guild_id: int, author_id: int, channel_id: int,
                 topic: str, description: str, cooldown: int = 1800,
                 now: int | None = None) -> Suggestion:
    now = int(time.time()) if now is None else now
    topic = clean(topic, minimum=4, maximum=100)
    description = clean(description, minimum=12, maximum=1200, multiline=True)
    if not 0 <= cooldown <= 86400:
        raise ValueError("Задержка между предложениями: от 0 до 86400 секунд.")
    async with db.session() as session:
        # INSERT ... ON CONFLICT WHERE держит write lock SQLite. Два
        # одновременных запроса не смогут обойти cooldown.
        stmt = sqlite_insert(SuggestionThrottle).values(
            guild_id=guild_id, user_id=author_id, last_created_at=now,
        ).on_conflict_do_update(
            index_elements=[SuggestionThrottle.guild_id, SuggestionThrottle.user_id],
            set_={"last_created_at": now},
            where=SuggestionThrottle.last_created_at <= now - cooldown,
        )
        changed = await session.execute(stmt)
        if changed.rowcount != 1:
            raise ValueError("Новая идея пока недоступна: действует задержка между публикациями.")
        row = Suggestion(
            guild_id=guild_id, author_id=author_id, channel_id=channel_id,
            topic=topic, description=description, status="new", status_reason="",
            votes_for=0, votes_against=0, panel_missing=False,
            created_at=now, updated_at=now,
        )
        session.add(row)
        await session.flush()
        session.add(SuggestionStatusEvent(
            suggestion_id=row.id, actor_id=author_id, old_status=None,
            new_status="new", reason="Предложение создано", created_at=now,
        ))
        suggestion_id = row.id
    return await get(db, guild_id, suggestion_id)


async def get(db, guild_id: int, suggestion_id: int) -> Suggestion | None:
    async with db.sessions() as session:
        return await session.scalar(select(Suggestion).where(
            Suggestion.guild_id == guild_id, Suggestion.id == suggestion_id,
        ))


async def list_items(db, guild_id: int, *, status: str | None = None,
                     limit: int = 20) -> list[Suggestion]:
    if status is not None and status not in STATES:
        raise ValueError("Неизвестный статус предложения.")
    async with db.sessions() as session:
        query = select(Suggestion).where(Suggestion.guild_id == guild_id)
        if status is not None:
            query = query.where(Suggestion.status == status)
        rows = await session.scalars(query.order_by(Suggestion.id.desc()).limit(limit))
        return list(rows.all())


async def published(db, guild_id: int, *, limit: int = 5000) -> list[Suggestion]:
    async with db.sessions() as session:
        rows = await session.scalars(select(Suggestion).where(
            Suggestion.guild_id == guild_id,
            Suggestion.message_id.is_not(None), Suggestion.panel_missing.is_(False),
        ).order_by(Suggestion.id.desc()).limit(limit))
        return list(rows.all())


async def unpublished(db, guild_id: int, *, limit: int = 100) -> list[Suggestion]:
    async with db.sessions() as session:
        rows = await session.scalars(select(Suggestion).where(
            Suggestion.guild_id == guild_id, Suggestion.message_id.is_(None),
        ).order_by(Suggestion.id).limit(limit))
        return list(rows.all())


async def vote(db, *, guild_id: int, suggestion_id: int, user_id: int, choice: int,
               now: int | None = None) -> tuple[bool, Suggestion]:
    """Один активный голос, повтор того же выбора ничего не меняет."""
    if choice not in (-1, 1):
        raise ValueError("Выберите «За» или «Против».")
    now = int(time.time()) if now is None else now
    async with db.session() as session:
        locked = await session.execute(update(Suggestion).where(
            Suggestion.id == suggestion_id, Suggestion.guild_id == guild_id,
            Suggestion.status.in_(OPEN_STATES),
        ).values(updated_at=now))
        if locked.rowcount != 1:
            raise ValueError("Предложение закрыто для голосования или не найдено.")
        row = await session.get(Suggestion, suggestion_id)
        current = await session.scalar(select(SuggestionVote).where(
            SuggestionVote.suggestion_id == suggestion_id, SuggestionVote.user_id == user_id,
        ))
        if current is not None and current.choice == choice:
            return False, row
        if current is None:
            session.add(SuggestionVote(
                suggestion_id=suggestion_id, user_id=user_id, choice=choice, updated_at=now,
            ))
        else:
            if current.choice == 1:
                row.votes_for -= 1
            else:
                row.votes_against -= 1
            current.choice = choice
            current.updated_at = now
        if choice == 1:
            row.votes_for += 1
        else:
            row.votes_against += 1
    return True, await get(db, guild_id, suggestion_id)


async def change_status(db, *, guild_id: int, suggestion_id: int, actor_id: int,
                        status: str, reason: str, now: int | None = None) -> Suggestion:
    if status not in STATES:
        raise ValueError("Недопустимый статус предложения.")
    reason = clean(reason, minimum=3, maximum=500)
    now = int(time.time()) if now is None else now
    async with db.session() as session:
        # Блокируем автора и голосующих на период записи решения
        # последовательным обновлением записи предложения.
        result = await session.execute(update(Suggestion).where(
            Suggestion.id == suggestion_id, Suggestion.guild_id == guild_id,
            Suggestion.status != status,
        ).values(updated_at=now))
        if result.rowcount != 1:
            raise ValueError("Предложение не найдено или этот статус уже установлен.")
        row = await session.get(Suggestion, suggestion_id)
        old_status = row.status
        row.status, row.status_reason, row.status_actor_id = status, reason, actor_id
        session.add(SuggestionStatusEvent(
            suggestion_id=suggestion_id, actor_id=actor_id,
            old_status=old_status, new_status=status, reason=reason, created_at=now,
        ))
        payload = json.dumps({
            "guild_id": guild_id, "suggestion_id": suggestion_id,
            "actor_id": actor_id, "old_status": old_status,
            "status": status, "reason": reason,
        }, ensure_ascii=False)
        session.add(OutboxEvent(
            id=f"suggestion-status-{suggestion_id}-{uuid4()}",
            kind="suggestions.status_changed", payload_json=payload, created_at=now,
        ))
    return await get(db, guild_id, suggestion_id)


async def history(db, guild_id: int, suggestion_id: int) -> list[SuggestionStatusEvent]:
    if await get(db, guild_id, suggestion_id) is None:
        raise ValueError("Предложение не найдено.")
    async with db.sessions() as session:
        rows = await session.scalars(select(SuggestionStatusEvent).where(
            SuggestionStatusEvent.suggestion_id == suggestion_id,
        ).order_by(SuggestionStatusEvent.id))
        return list(rows.all())


async def set_message(db, guild_id: int, suggestion_id: int, message_id: int) -> bool:
    async with db.session() as session:
        result = await session.execute(update(Suggestion).where(
            Suggestion.id == suggestion_id, Suggestion.guild_id == guild_id,
            (Suggestion.message_id.is_(None) | Suggestion.panel_missing.is_(True)),
        ).values(message_id=message_id, panel_missing=False))
        return result.rowcount == 1


async def set_thread(db, guild_id: int, suggestion_id: int,
                     thread_id: int, *, previous: int | None = None) -> bool:
    async with db.session() as session:
        result = await session.execute(update(Suggestion).where(
            Suggestion.id == suggestion_id, Suggestion.guild_id == guild_id,
            Suggestion.thread_id == previous if previous is not None
            else Suggestion.thread_id.is_(None),
        ).values(thread_id=thread_id))
        return result.rowcount == 1


async def register_panel(db, guild_id: int, channel_id: int, message_id: int,
                         now: int | None = None) -> None:
    now = int(time.time()) if now is None else now
    async with db.session() as session:
        session.add(SuggestionCreatePanel(
            guild_id=guild_id, channel_id=channel_id,
            message_id=message_id, missing=False, created_at=now,
        ))


async def deleted_panel(db, guild_id: int, channel_id: int,
                        message_id: int, *, now: int | None = None) -> str | None:
    """Помечаем удалённую карточку и ставим предупреждение в durable outbox."""
    now = int(time.time()) if now is None else now
    async with db.session() as session:
        result = await session.execute(update(Suggestion).where(
            Suggestion.guild_id == guild_id, Suggestion.channel_id == channel_id,
            Suggestion.message_id == message_id, Suggestion.panel_missing.is_(False),
        ).values(panel_missing=True, updated_at=now))
        if result.rowcount:
            suggestion_id = await session.scalar(select(Suggestion.id).where(
                Suggestion.guild_id == guild_id, Suggestion.channel_id == channel_id,
                Suggestion.message_id == message_id,
            ))
            kind, target_id = "suggestion", suggestion_id
        else:
            result = await session.execute(update(SuggestionCreatePanel).where(
                SuggestionCreatePanel.guild_id == guild_id,
                SuggestionCreatePanel.channel_id == channel_id,
                SuggestionCreatePanel.message_id == message_id,
                SuggestionCreatePanel.missing.is_(False),
            ).values(missing=True))
            if not result.rowcount:
                return None
            kind, target_id = "create_panel", message_id
        event_id = f"suggestion-panel-deleted-{guild_id}-{message_id}"
        payload = json.dumps({
            "guild_id": guild_id, "kind": kind, "target_id": target_id,
            "channel_id": channel_id, "message_id": message_id,
        }, ensure_ascii=False)
        stmt = sqlite_insert(OutboxEvent).values(
            id=event_id, kind="suggestions.panel_deleted",
            payload_json=payload, created_at=now,
        ).on_conflict_do_nothing(index_elements=[OutboxEvent.id])
        await session.execute(stmt)
        return kind


async def missing_count(db, guild_id: int) -> tuple[int, int]:
    async with db.sessions() as session:
        suggestions = await session.scalar(select(func.count()).select_from(Suggestion).where(
            Suggestion.guild_id == guild_id, Suggestion.panel_missing.is_(True),
        ))
        panels = await session.scalar(select(func.count()).select_from(SuggestionCreatePanel).where(
            SuggestionCreatePanel.guild_id == guild_id, SuggestionCreatePanel.missing.is_(True),
        ))
        return suggestions or 0, panels or 0
