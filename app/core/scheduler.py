"""Устойчивый планировщик: SQLite leases, ограниченные повторы и диагностика."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import func, or_, select, update
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
        self._run_lock = asyncio.Lock()
        self.failures = 0
        self.last_error: str | None = None
        self._missing_logged: set[str] = set()

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

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
            # Завершённые ключи не удаляем: это барьер против повторной отправки.
            await session.execute(stmt.on_conflict_do_nothing(index_elements=[ScheduledJob.job_key]))

    async def status(self) -> dict[str, int]:
        now = int(time.time())
        async with self.db.sessions() as session:
            rows = (await session.execute(
                select(ScheduledJob.status, func.count()).group_by(ScheduledJob.status)
            )).all()
            overdue = await session.scalar(select(func.count()).select_from(ScheduledJob).where(
                ScheduledJob.status == "pending", ScheduledJob.due_at <= now))
            stale = await session.scalar(select(func.count()).select_from(ScheduledJob).where(
                ScheduledJob.status == "running", ScheduledJob.locked_until <= now))
            names = (await session.scalars(select(ScheduledJob.handler).where(
                ScheduledJob.status.in_(("pending", "running"))).distinct())).all()
        result = {"pending": 0, "running": 0, "failed": 0, "done": 0,
                  "overdue": overdue or 0, "stale": stale or 0,
                  "missing_handlers": len(set(names) - self.handlers.keys())}
        result.update(dict(rows))
        return result

    async def run_due(self) -> int:
        """Один проход в процессе; операции Discord обязаны быть идемпотентными.

        Lease в БД обеспечивает возврат задачи после обрыва, но не exactly-once
        для внешнего API: задача может завершиться в Discord до фиксации результата.
        """
        if self._run_lock.locked():
            return 0
        async with self._run_lock:
            return await self._run_due_locked()

    async def _run_due_locked(self) -> int:
        now = int(time.time())
        async with self.db.sessions() as session:
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
                if handler_name not in self._missing_logged:
                    log.error("Задачи %s остались без обработчика; сохранены для восстановления", handler_name)
                    self._missing_logged.add(handler_name)
                continue
            self._missing_logged.discard(handler_name)
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
            except asyncio.CancelledError:
                # Lease сохраняется в SQLite и будет повторно захвачен после таймаута.
                log.warning("Остановка во время задачи id=%s handler=%s", job_id, handler_name)
                raise
            except Exception as exc:
                self.failures += 1
                self.last_error = f"{handler_name} #{job_id}: {type(exc).__name__}"
                log.exception("Ошибка выполнения задачи id=%s handler=%s", job_id, handler_name)
                async with self.db.session() as session:
                    row = await session.get(ScheduledJob, job_id)
                    if row and row.status == "running":
                        row.status = "failed" if row.attempts >= 5 else "pending"
                        row.locked_until = 0
                        if row.status == "pending":
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
            except Exception as exc:
                self.failures += 1
                self.last_error = f"loop: {type(exc).__name__}"
                log.exception("Ошибка планировщика")
            await asyncio.sleep(self.interval)

    def start(self) -> None:
        if self.running:
            return
        if self._task is not None:
            log.error("Перезапуск остановившегося процесса планировщика")
        self._task = asyncio.create_task(self._loop(), name="newsqad-scheduler")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
