"""v1.4.0 — целостность, восстановление очередей и контракт healthcheck."""
from __future__ import annotations

import json

import pytest
from sqlalchemy import update

from app.core.database import Database, migrate
from app.core.event_bus import EventBus
from app.core.scheduler import Scheduler
from app.healthcheck import is_healthy
from app.models import EventReceipt, ScheduledJob
from app.ops.backup import restore, snapshot, verify


def db_url(tmp_path):
    return f"sqlite+aiosqlite:///{tmp_path / 'v14.db'}"


@pytest.mark.asyncio
async def test_existing_database_migrates_and_retains_settings(tmp_path):
    url = db_url(tmp_path)
    migrate(url)
    db = Database(url)
    try:
        await db.set_setting(100, "important", {"preserve": True}, actor_id=1)
        assert await db.check_integrity()
    finally:
        await db.close()
    migrate(url)
    migrated = Database(url)
    try:
        assert await migrated.check_integrity()
        assert await migrated.get_setting(100, "important") == {"preserve": True}
    finally:
        await migrated.close()


@pytest.mark.asyncio
async def test_scheduler_recovers_failed_and_stale_jobs(tmp_path):
    migrate(db_url(tmp_path))
    db = Database(db_url(tmp_path))
    calls = []

    async def handler(payload):
        calls.append(payload["n"])
        if len(calls) == 1:
            raise RuntimeError("temporary outage")

    try:
        scheduler = Scheduler(db, interval=3600)
        scheduler.register("demo", handler)
        await scheduler.schedule(job_key="unique", handler="demo", due_at=1, payload={"n": 7})
        assert await scheduler.run_due() == 0
        state = await scheduler.status()
        assert state["pending"] == 1 and scheduler.failures == 1
        async with db.session() as session:
            await session.execute(update(ScheduledJob).where(
                ScheduledJob.job_key == "unique").values(due_at=1))
        assert await scheduler.run_due() == 1
        assert await scheduler.run_due() == 0
        assert calls == [7, 7]
        assert (await scheduler.status())["done"] == 1

        await scheduler.schedule(job_key="stale", handler="demo", due_at=1, payload={"n": 8})
        async with db.session() as session:
            await session.execute(update(ScheduledJob).where(
                ScheduledJob.job_key == "stale").values(status="running", locked_until=1))
        assert (await scheduler.status())["stale"] == 1
        assert await scheduler.run_due() == 1
        assert calls[-1] == 8
        scheduler.start()
        first = scheduler._task
        scheduler.start()
        assert scheduler._task is first
        await scheduler.stop()
        assert not scheduler.running
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_scheduler_parallel_passes_do_not_duplicate(tmp_path):
    import asyncio

    migrate(db_url(tmp_path))
    db = Database(db_url(tmp_path))
    calls = []

    async def handler(payload):
        calls.append(payload)
        await asyncio.sleep(0.05)

    try:
        scheduler = Scheduler(db)
        scheduler.register("demo", handler)
        await scheduler.schedule(job_key="parallel", handler="demo", due_at=1, payload={"ok": True})
        results = await asyncio.gather(scheduler.run_due(), scheduler.run_due())
        assert sum(results) == 1
        assert len(calls) == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_outbox_skips_blocked_first_page_without_losing_events(tmp_path):
    migrate(db_url(tmp_path))
    db = Database(db_url(tmp_path))
    received = []

    async def handler(payload):
        received.append(payload["value"])

    try:
        bus = EventBus(db)
        bus.subscribe("known", "handler", handler)
        for index in range(101):
            await bus.publish("unknown", {"i": index}, event_id=f"unknown-{index:04d}")
        await bus.publish("known", {"value": 42}, event_id="zz-known")
        assert await bus.dispatch_pending(scan_limit=200) == 1
        assert received == [42]
        state = await bus.status()
        assert state["queued"] == 101
        assert state["unhandled_kinds"] == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_receipt_pruning_preserves_pending_event_receipts(tmp_path):
    migrate(db_url(tmp_path))
    db = Database(db_url(tmp_path))
    try:
        bus = EventBus(db)
        await bus.publish("known", {"value": 1}, event_id="live")
        async with db.session() as session:
            session.add(EventReceipt(event_id="live", handler="first", completed_at=100))
            session.add(EventReceipt(event_id="finished", handler="first", completed_at=100))
        removed = await bus.prune_receipts(days=1, now=200000)
        assert removed == 1
        assert (await bus.status())["receipts"] == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_backup_restore_keeps_existing_database(tmp_path):
    url = db_url(tmp_path)
    migrate(url)
    db = Database(url)
    try:
        await db.set_setting(12, "before_backup", 123, actor_id=1)
    finally:
        await db.close()
    source = tmp_path / "v14.db"
    backup = tmp_path / "backup.db"
    snapshot(source, backup)
    verify(backup)
    db = Database(url)
    try:
        await db.set_setting(12, "before_backup", 456, actor_id=1)
    finally:
        await db.close()
    restore(backup, source)
    db = Database(url)
    try:
        assert await db.get_setting(12, "before_backup") == 123
        assert await db.check_integrity()
    finally:
        await db.close()


def test_v14_health_requires_active_background_core(tmp_path):
    path = tmp_path / "health.json"
    payload = {
        "ready": True, "timestamp": 100, "scheduler": True,
        "event_bus": True, "discord": True, "sqlite": True,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert is_healthy(path, now=101)
    for field in ("scheduler", "event_bus", "discord", "sqlite"):
        changed = dict(payload)
        changed[field] = False
        path.write_text(json.dumps(changed), encoding="utf-8")
        assert not is_healthy(path, now=101)
    assert not is_healthy(path, now=500)
