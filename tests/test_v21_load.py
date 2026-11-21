"""Repeatable SQLite WAL and scheduler load test; no external Discord calls."""
import asyncio
import statistics
import time

import pytest

from app.config import GUILD_ID
from app.core.database import Database, migrate
from app.core.scheduler import Scheduler


@pytest.mark.asyncio
async def test_sqlite_concurrency_and_scheduler_claiming(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'load.db'}"
    migrate(url)
    db = Database(url)
    scheduler = Scheduler(db, interval=0.01)
    received = []
    latency_ms = []

    async def handler(payload):
        received.append(payload["id"])

    scheduler.register("load.dummy", handler)
    try:
        async def writer(index):
            start = time.perf_counter()
            await db.set_setting(GUILD_ID, f"benchmark.item.{index}", index, actor_id=11)
            latency_ms.append((time.perf_counter() - start) * 1000)

        await asyncio.gather(*(writer(index) for index in range(40)))
        assert await asyncio.gather(*(db.get_setting(GUILD_ID, f"benchmark.item.{i}") for i in range(40))) == list(range(40))
        now = int(time.time()) - 1
        await asyncio.gather(*(scheduler.schedule(
            job_key=f"benchmark:{i}", handler="load.dummy",
            due_at=now, payload={"id": i}) for i in range(75)))
        for _ in range(5):
            await scheduler.run_due()
        assert len(received) == len(set(received)) == 75
        assert (await scheduler.status())["done"] >= 75
        assert scheduler.failures == 0
        print("SQLite concurrent writes=40; p50_ms=%.1f; p95_ms=%.1f; max_ms=%.1f; scheduled=75" %
              (statistics.median(latency_ms), sorted(latency_ms)[37], max(latency_ms)))
    finally:
        await db.close()
