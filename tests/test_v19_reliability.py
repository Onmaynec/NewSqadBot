"""v1.9.0 reliability: snapshots, journal, retry and concurrent database transitions."""
from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path

import pytest
from sqlalchemy import select, update

from app.config import GUILD_ID
from app.core.database import Database, migrate
from app.core.observability import OperationalJournal
from app.core.scheduler import Scheduler
from app.models import ScheduledJob
from app.ops.backup import BackupError
from app.ops.automatic_backup import (
    BackupSettings, BackupWorker, create_automatic_backup, restore_probe, rotation,
)
from app.services import applications, tickets


def url(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path}"


def test_journal_recovers_gateway_history_without_sensitive_data(tmp_path):
    path = tmp_path / "operations.json"
    journal = OperationalJournal(path)
    journal.record("gateway.connected", now=100)
    journal.record("gateway.disconnected", now=140)
    journal.record("gateway.resumed", now=160)
    journal.http_failure(429)
    assert journal.http_errors == journal.http_rate_limits == 1
    state = journal.snapshot()
    assert state["last_connected_at"] == 160
    assert state["last_disconnected_at"] == 140
    assert len(state["history"]) == 4
    reloaded = OperationalJournal(path)
    assert reloaded.last_connected_at == 160
    assert reloaded.last_disconnected_at == 140
    assert reloaded.http_rate_limits == 1
    with pytest.raises(ValueError):
        journal.record("discord.message.content")


def test_snapshot_export_rotation_and_real_restore_probe(tmp_path):
    dbfile = tmp_path / "live" / "newsqad.db"
    dbfile.parent.mkdir()
    migrate(url(dbfile))
    export = tmp_path / "host-export"
    export.mkdir()
    settings = BackupSettings(interval_hours=24, keep=2, export_dir=export)

    for _ in range(3):
        local, outside = create_automatic_backup(dbfile, settings)
        assert outside is not None
        assert local.is_file() and outside.is_file()
        restore_probe(outside)
    assert len(list((dbfile.parent / "backups").glob("newsqad-auto-*.db"))) == 2
    assert len(list(export.glob("newsqad-auto-*.db"))) == 2
    manual = export / "newsqad-manual.db"
    manual.write_text("manual")
    rotation(export, keep=1)
    assert manual.read_text() == "manual"
    assert len(list(export.glob("newsqad-auto-*.db"))) == 1
    with sqlite3.connect(outside) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()


def test_off_volume_export_must_not_point_inside_active_volume(tmp_path):
    dbfile = tmp_path / "data" / "newsqad.db"
    dbfile.parent.mkdir()
    migrate(url(dbfile))
    with pytest.raises(BackupError, match="Экспорт должен"):
        create_automatic_backup(dbfile, BackupSettings(export_dir=dbfile.parent / "backups"))


@pytest.mark.asyncio
async def test_backup_worker_status_and_database_query_metrics(tmp_path):
    dbfile = tmp_path / "db.db"
    migrate(url(dbfile))
    db = Database(url(dbfile))
    try:
        assert await db.ping()
        assert db.query_metrics()["queries"] >= 1
        assert db.query_metrics()["last_ms"] >= 0
    finally:
        await db.close()
    worker = BackupWorker(dbfile, BackupSettings(export_dir=None))
    assert await worker.run_once()
    assert worker.last_verified is not None
    assert worker.status()["failures"] == 0


@pytest.mark.asyncio
async def test_concurrent_application_review_has_one_winner(tmp_path):
    dbfile = tmp_path / "applications.db"
    migrate(url(dbfile))
    db = Database(url(dbfile))
    try:
        item = await applications.submit(
            db, guild_id=GUILD_ID, user_id=88, username="member", name="Roman",
            gender="male", age=20, inviter="friend",
        )
        outcomes = await asyncio.gather(*(
            applications.transition(
                db, item.id, expected=("pending",), target="in_review",
                actor_id=staff, reason="Взял в работу",
            ) for staff in (10, 11, 12)
        ))
        assert outcomes.count(True) == 1
        latest = await applications.get_application(db, item.id)
        assert latest.status == "in_review"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_concurrent_ticket_claim_has_one_winner(tmp_path):
    dbfile = tmp_path / "tickets.db"
    migrate(url(dbfile))
    db = Database(url(dbfile))
    try:
        await tickets.seed_categories(db, GUILD_ID)
        category = (await tickets.categories(db, GUILD_ID))[0]
        item = await tickets.reserve(db, GUILD_ID, 90, category.id, "Помощь", "Описание",
                                     support_role_id=101)
        assert await tickets.created(db, GUILD_ID, item.id, 222)
        outcomes = await asyncio.gather(*(
            tickets.claim(db, GUILD_ID, item.id, staff)
            for staff in (10, 11, 12)
        ))
        assert outcomes.count(True) == 1
        current = await tickets.get(db, GUILD_ID, item.id)
        assert current.assignee_id in (10, 11, 12)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_scheduler_failure_retry_and_restart_without_duplicate_job(tmp_path):
    dbfile = tmp_path / "scheduler.db"
    migrate(url(dbfile))
    db = Database(url(dbfile))
    attempts = []
    try:
        scheduler = Scheduler(db)
        async def callback(_payload):
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("Discord temporarily unavailable")

        scheduler.register("test.discord", callback)
        now = int(time.time())
        await scheduler.schedule(job_key="dedup:1", handler="test.discord",
                                 due_at=now - 1, payload={"test": True})
        await scheduler.run_due()
        assert attempts == [1]
        # Simulate process restart and due-time expiration of the retry.
        restarted = Scheduler(db)
        restarted.register("test.discord", callback)
        async with db.session() as session:
            await session.execute(update(ScheduledJob).where(
                ScheduledJob.job_key == "dedup:1").values(due_at=now - 1))
        results = await asyncio.gather(*(restarted.run_due() for _ in range(3)))
        assert sum(results) == 1
        assert attempts == [1, 1]
        await restarted.schedule(job_key="dedup:1", handler="test.discord",
                                 due_at=now - 1, payload={"test": True})
        assert await restarted.run_due() == 0
        async with db.sessions() as session:
            row = await session.scalar(select(ScheduledJob).where(ScheduledJob.job_key == "dedup:1"))
            assert row.status == "done"
            assert row.attempts == 2
    finally:
        await db.close()
