from pathlib import Path

import pytest

from app.core.database import Database, migrate
from app.core.event_bus import EventBus
from app.core.scheduler import Scheduler

pytest.importorskip("aiosqlite")


@pytest.mark.asyncio
async def test_settings_and_audit(tmp_path: Path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'settings.db'}"
    migrate(url)
    db = Database(url)
    try:
        assert await db.get_setting(123, "member_role_id") is None
        await db.set_setting(123, "member_role_id", 42, actor_id=4)
        assert await db.get_setting(123, "member_role_id") == 42
        await db.set_setting(123, "member_role_id", 43, actor_id=4)
        assert await db.get_setting(123, "member_role_id") == 43
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_scheduler_claims_once(tmp_path: Path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'scheduler.db'}"
    migrate(url)
    db = Database(url)
    called = []
    try:
        scheduler = Scheduler(db)

        async def execute(payload):
            called.append(payload)

        scheduler.register("example", execute)
        await scheduler.schedule(job_key="same-key", handler="example", due_at=1, payload={"x": 1})
        await scheduler.schedule(job_key="same-key", handler="example", due_at=1, payload={"x": 2})
        assert await scheduler.run_due() == 1
        assert await scheduler.run_due() == 0
        assert called == [{"x": 1}]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_event_bus_deduplicates_subscribers(tmp_path: Path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'events.db'}"
    migrate(url)
    db = Database(url)
    received = []
    try:
        bus = EventBus(db)

        async def handler(payload):
            received.append(payload)

        bus.subscribe("member_approved", "example_handler", handler)
        await bus.publish("member_approved", {"id": 1}, event_id="sample-1")
        await bus.publish("member_approved", {"id": 1}, event_id="sample-1")
        assert await bus.dispatch_pending() == 1
        assert await bus.dispatch_pending() == 0
        assert received == [{"id": 1}]
    finally:
        await db.close()
