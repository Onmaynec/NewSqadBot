"""Устойчивый планировщик для будущих модулей (один экземпляр бота)."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.core.database import Database
from app.models import ScheduledJob

log = logging.getLogger(__name__)
Handler = Callable[[dict[str, Any]], Awaitable[None]]


class Scheduler:
    def __init__(self, db: Database, *, interval: float = 10.0):
        self.db = db
        self.interval = interval
        self.handlers: dict[str, Handler] = {}
        self._task: asyncio.Task[None] | None = None

    def register(self, key: str, handler: Handler) -> None:
        if key in self.handlers:
            raise ValueError(f"Планировщик {key!r} зарегистрирован дважды")
        self.handlers[key] = handler

    async def schedule(self, *, job_key: str, handler: str, due_at: int, payload: dict[str, Any]) -> None:
        if handler not in self.handlers:
            raise ValueError(f"Неизвестный обработчик задачи: {handler}")
        async with self.db.session() as session:
            stmt = sqlite_insert(ScheduledJob).values(
                job_key=job_key, handler=handler, due_at=due_at,
                payload_json=json.dumps(payload, ensure_ascii=False),
                status="pending", attempts=0, locked_until=0,
            )
            await session.execute(stmt.on_conflict_do_nothing(index_elements=[ScheduledJob.job_key]))

    async def run_due(self) -> int:
        now = int(time.time())
        async with self.db.sessions() as session:
            # Не держим БД-транзакцию открытой во время внешнего обработчика.
            due = (await session.scalars(select(ScheduledJob).where(
                or_(
                    (ScheduledJob.status == "pending") & (ScheduledJob.due_at <= now),
                    (ScheduledJob.status == "running") & (ScheduledJob.locked_until <= now),
                )
            ).order_by(ScheduledJob.due_at).limit(25))).all()
            pending = [(job.id, job.handler, job.payload_json) for job in due]
        completed = 0
        for job_id, handler_name, payload_json in pending:
            if handler_name not in self.handlers:
                log.warning("Обработчик задачи %s не загружен; задача сохранена", handler_name)
                continue
            async with self.db.session() as session:
                result = await session.execute(update(ScheduledJob).where(
                    ScheduledJob.id == job_id,
                    or_(
                        (ScheduledJob.status == "pending") & (ScheduledJob.due_at <= now),
                        (ScheduledJob.status == "running") & (ScheduledJob.locked_until <= now),
                    ),
                ).values(status="running", locked_until=now + 120, attempts=ScheduledJob.attempts + 1))
                claimed = result.rowcount == 1
            if not claimed:
                continue
            try:
                await self.handlers[handler_name](json.loads(payload_json))
            except Exception:
                log.exception("Ошибка выполнения отложенной задачи id=%s", job_id)
                async with self.db.session() as session:
                    row = await session.get(ScheduledJob, job_id)
                    if row and row.status == "running":
                        row.status = "failed" if row.attempts >= 5 else "pending"
                        row.locked_until = 0
                        row.due_at = int(time.time()) + min(3600, 30 * (2 ** (row.attempts - 1)))
            else:
                async with self.db.session() as session:
                    row = await session.get(ScheduledJob, job_id)
                    if row and row.status == "running":
                        row.status = "done"
                        row.locked_until = 0
                completed += 1
        return completed

    async def _loop(self) -> None:
        while True:
            try:
                await self.run_due()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Ошибка планировщика")
            await asyncio.sleep(self.interval)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="newsqad-scheduler")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
