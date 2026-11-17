"""Durable outbox с квитанциями, постраничной обработкой и безопасной очисткой."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, delete, exists, func, or_, select
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
        self._dispatch_lock = asyncio.Lock()
        self._scan_cursor: tuple[int, str] | None = None
        self.failures = 0
        self.last_error: str | None = None
        self._unhandled_logged: set[str] = set()

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

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

    async def status(self) -> dict[str, int]:
        async with self.database.sessions() as session:
            queue = await session.scalar(select(func.count()).select_from(OutboxEvent))
            receipts = await session.scalar(select(func.count()).select_from(EventReceipt))
            kinds = (await session.scalars(select(OutboxEvent.kind).distinct())).all()
        return {"queued": queue or 0, "receipts": receipts or 0,
                "unhandled_kinds": len(set(kinds) - self.handlers.keys() - {"leveling.xp_awarded"})}

    async def prune_receipts(self, *, days: int = 30, now: int | None = None) -> int:
        """Сохраняем квитанции ВСЕХ ещё ожидающих событий, даже старых."""
        if days < 1:
            raise ValueError("Минимальное хранение квитанций — 1 день")
        cutoff = (int(time.time()) if now is None else now) - days * 86400
        async with self.database.session() as session:
            statement = delete(EventReceipt).where(
                EventReceipt.completed_at < cutoff,
                ~exists(select(OutboxEvent.id).where(OutboxEvent.id == EventReceipt.event_id)),
            )
            result = await session.execute(statement)
            return result.rowcount or 0

    async def dispatch_pending(self, *, scan_limit: int = 500) -> int:
        """At-least-once: побочные эффекты обработчиков должны быть идемпотентны."""
        if self._dispatch_lock.locked():
            return 0
        async with self._dispatch_lock:
            return await self._dispatch_locked(scan_limit=scan_limit)

    async def _dispatch_locked(self, *, scan_limit: int) -> int:
        count = 0
        cursor: tuple[int, str] | None = self._scan_cursor
        for _ in range(max(1, (scan_limit + 99) // 100)):
            async with self.database.sessions() as session:
                query = select(OutboxEvent).order_by(OutboxEvent.created_at, OutboxEvent.id).limit(100)
                if cursor is not None:
                    query = query.where(or_(
                        OutboxEvent.created_at > cursor[0],
                        and_(OutboxEvent.created_at == cursor[0], OutboxEvent.id > cursor[1]),
                    ))
                records = (await session.scalars(query)).all()
                events = [(r.id, r.kind, r.payload_json, r.created_at) for r in records]
            if not events:
                cursor = None
                break
            for event_id, kind, payload_json, created_at in events:
                cursor = (created_at, event_id)
                subscribers = self.handlers.get(kind, {})
                if not subscribers:
                    if kind == "leveling.xp_awarded":
                        async with self.database.session() as session:
                            await session.execute(delete(OutboxEvent).where(OutboxEvent.id == event_id))
                    elif kind not in self._unhandled_logged:
                        logger.error("События %s ожидают отсутствующий модуль; сохраняем outbox", kind)
                        self._unhandled_logged.add(kind)
                    continue
                self._unhandled_logged.discard(kind)
                for name, handler in subscribers.items():
                    async with self.database.sessions() as session:
                        delivered = await session.scalar(select(EventReceipt.id).where(
                            EventReceipt.event_id == event_id, EventReceipt.handler == name))
                    if delivered is not None:
                        continue
                    try:
                        await handler(json.loads(payload_json))
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        self.failures += 1
                        self.last_error = f"{kind}/{name}: {type(exc).__name__}"
                        logger.exception("Ошибка события %s обработчик %s", event_id, name)
                        continue
                    async with self.database.session() as session:
                        stmt = sqlite_insert(EventReceipt).values(
                            event_id=event_id, handler=name, completed_at=int(time.time()))
                        await session.execute(stmt.on_conflict_do_nothing(
                            index_elements=[EventReceipt.event_id, EventReceipt.handler]))
                    count += 1
                async with self.database.sessions() as session:
                    delivered_names = (await session.scalars(select(EventReceipt.handler).where(
                        EventReceipt.event_id == event_id))).all()
                if set(subscribers) <= set(delivered_names):
                    async with self.database.session() as session:
                        await session.execute(delete(OutboxEvent).where(OutboxEvent.id == event_id))
            if len(events) < 100:
                cursor = None
                break
        # Не даём неисправным первым 500 событиям блокировать всю очередь.
        self._scan_cursor = cursor
        return count

    async def _loop(self) -> None:
        ticks = 0
        while True:
            try:
                await self.dispatch_pending()
                ticks += 1
                if ticks % 180 == 0:
                    removed = await self.prune_receipts()
                    if removed:
                        logger.info("Удалено устаревших квитанций событий: %s", removed)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.failures += 1
                self.last_error = f"loop: {type(exc).__name__}"
                logger.exception("Ошибка доставки внутренних событий")
            await asyncio.sleep(10)

    def start(self) -> None:
        if self.running:
            return
        if self._task is not None:
            logger.error("Перезапуск остановившегося EventBus")
        self._task = asyncio.create_task(self._loop(), name="newsqad-events")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
