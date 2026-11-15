"""События ядра: durable outbox + квитанции доставок каждому обработчику."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.core.database import Database
from app.models import EventReceipt, OutboxEvent

logger = logging.getLogger(__name__)
EventHandler = Callable[[dict[str, Any]], Awaitable[None]]


class EventBus:
    def __init__(self, database: Database):
        self.database = database
        self.handlers: dict[str, dict[str, EventHandler]] = defaultdict(dict)
        self._task: asyncio.Task[None] | None = None

    def subscribe(self, kind: str, name: str, handler: EventHandler) -> None:
        if name in self.handlers[kind]:
            raise ValueError(f"Дублирующийся обработчик: {kind}.{name}")
        self.handlers[kind][name] = handler

    async def publish(self, kind: str, payload: dict[str, Any], *, event_id: str | None = None) -> str:
        event_id = event_id or str(uuid4())
        async with self.database.session() as session:
            stmt = sqlite_insert(OutboxEvent).values(
                id=event_id, kind=kind, payload_json=json.dumps(payload, ensure_ascii=False),
                created_at=int(time.time()),
            )
            await session.execute(stmt.on_conflict_do_nothing(index_elements=[OutboxEvent.id]))
        return event_id

    async def dispatch_pending(self) -> int:
        """Доставляет события минимум один раз; внешние side-effects должны быть идемпотентны."""
        count = 0
        async with self.database.sessions() as session:
            records = (await session.scalars(select(OutboxEvent).order_by(OutboxEvent.created_at).limit(100))).all()
            events = [(record.id, record.kind, record.payload_json) for record in records]
        for event_id, kind, payload_json in events:
            subscribers = self.handlers.get(kind, {})
            if not subscribers:
                continue
            for name, handler in subscribers.items():
                async with self.database.sessions() as session:
                    exists = await session.scalar(select(EventReceipt.id).where(
                        EventReceipt.event_id == event_id, EventReceipt.handler == name
                    ))
                if exists is not None:
                    continue
                try:
                    await handler(json.loads(payload_json))
                except Exception:
                    logger.exception("Ошибка обработки события %s обработчиком %s", event_id, name)
                    continue
                async with self.database.session() as session:
                    stmt = sqlite_insert(EventReceipt).values(
                        event_id=event_id, handler=name, completed_at=int(time.time())
                    )
                    await session.execute(stmt.on_conflict_do_nothing(index_elements=[EventReceipt.event_id, EventReceipt.handler]))
                count += 1
            # Завершённые события удаляем из очереди, иначе первые 100
            # записей навсегда заблокируют доставку более новых.
            async with self.database.sessions() as session:
                delivered = (await session.scalars(select(EventReceipt.handler).where(
                    EventReceipt.event_id == event_id
                ))).all()
            if set(subscribers) <= set(delivered):
                async with self.database.session() as session:
                    await session.execute(delete(OutboxEvent).where(OutboxEvent.id == event_id))
        return count

    async def _loop(self) -> None:
        while True:
            try:
                await self.dispatch_pending()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Ошибка доставки внутренних событий")
            await asyncio.sleep(10)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="newsqad-events")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
