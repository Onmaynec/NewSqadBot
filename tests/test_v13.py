"""v0.13.0 — агрегаты Statistics, приватность и отсутствие повторного счёта."""
from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.config import GUILD_ID, VERSION
from app.core.catalog import implemented
from app.core.database import Database, migrate
from app.models import (
    Application,
    ApplicationEvent,
    MemberProfile,
    StatsDay,
    Wallet,
)
from app.modules.statistics import Statistics, graph
from app.services import statistics as stats


def url(tmp_path, name="stats.db"):
    return f"sqlite+aiosqlite:///{tmp_path / name}"


def test_schema_and_command_contract(tmp_path):
    migrate(url(tmp_path))
    migrate(url(tmp_path))
    assert VERSION == "0.13.0"
    assert implemented("statistics")
    with sqlite3.connect(tmp_path / "stats.db") as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0012_statistics"
        names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {
            "statistics_daily", "statistics_receipts", "statistics_server_daily",
            "statistics_presence", "statistics_panels",
        } <= names
    assert {x.name for x in Statistics.stats.commands} >= {
        "server", "user", "top", "activity", "settings", "configure", "panel",
    }


def test_timezone_calendar_window_and_chart():
    ts = int(datetime(2026, 11, 16, 18, tzinfo=timezone.utc).timestamp())
    period = stats.period_bounds("7d", timezone="Europe/Tallinn", now=ts)
    assert period.first == "2026-11-10"
    assert period.last == "2026-11-16"
    assert len(["x" for _ in range(7)]) == 7
    assert (period.end_timestamp - period.start_timestamp) >= 7 * 23 * 3600
    with pytest.raises(ValueError):
        stats.period_bounds("one_month", timezone="UTC")
    rows = [
        ("2026-11-14", 1, 15, 1, 0),
        ("2026-11-15", 3, 20, 1, 1),
        ("2026-11-16", 0, 0, 0, 0),
    ]
    text = graph(rows, "7d", "messages")
    assert "▰" in text and "3" in text
    assert "W" in graph(rows, "30d", "voice")


@pytest.mark.asyncio
async def test_messages_voice_dedup_concurrency_and_pruning(tmp_path):
    database = Database(url(tmp_path))
    migrate(url(tmp_path))
    base = int(datetime(2026, 11, 16, 16, tzinfo=timezone.utc).timestamp())
    try:
        values = await asyncio.gather(*[
            stats.record_activity(database, guild_id=GUILD_ID, user_id=10,
                kind="message", reference=900, timestamp=base, timezone="Europe/Tallinn")
            for _ in range(8)
        ])
        assert sum(values) == 1
        assert await stats.record_activity(database, guild_id=GUILD_ID, user_id=10,
            kind="message", reference=901, timestamp=base, timezone="Europe/Tallinn")
        assert await stats.record_activity(database, guild_id=GUILD_ID, user_id=10,
            kind="voice", reference=base // 60, timestamp=base, timezone="Europe/Tallinn")
        assert not await stats.record_activity(database, guild_id=GUILD_ID, user_id=10,
            kind="voice", reference=base // 60, timestamp=base, timezone="Europe/Tallinn")
        with pytest.raises(ValueError):
            await stats.record_activity(database, guild_id=GUILD_ID, user_id=10,
                kind="unknown", reference=1, timestamp=base, timezone="UTC")
        old = stats.period_bounds("7d", timezone="Europe/Tallinn", now=base)
        values = await stats.activity_series(database, GUILD_ID, old)
        assert sum(row[1] for row in values) == 2
        assert sum(row[2] for row in values) == 1
        assert await stats.prune_receipts(database, now=base + 36 * 86400) == 3
        values = await stats.activity_series(database, GUILD_ID, old)
        assert sum(row[1] for row in values) == 2
        assert sum(row[2] for row in values) == 1
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_presence_baseline_and_duplicate_events(tmp_path):
    migrate(url(tmp_path))
    db = Database(url(tmp_path))
    now = int(datetime(2026, 11, 16, 16, tzinfo=timezone.utc).timestamp())
    try:
        await stats.bootstrap_presence(db, GUILD_ID, [1, 2], now=now)
        assert not await stats.record_presence(db, guild_id=GUILD_ID, user_id=1,
            joined=True, timezone="UTC", now=now + 1)
        assert await stats.record_presence(db, guild_id=GUILD_ID, user_id=1,
            joined=False, timezone="UTC", now=now + 10)
        assert not await stats.record_presence(db, guild_id=GUILD_ID, user_id=1,
            joined=False, timezone="UTC", now=now + 11)
        assert await stats.record_presence(db, guild_id=GUILD_ID, user_id=1,
            joined=True, timezone="UTC", now=now + 20)
        assert not await stats.record_presence(db, guild_id=GUILD_ID, user_id=1,
            joined=True, timezone="UTC", now=now + 21)
        assert await stats.record_presence(db, guild_id=GUILD_ID, user_id=3,
            joined=True, timezone="UTC", now=now + 22)
        period = stats.period_bounds("7d", timezone="UTC", now=now + 22)
        rows = await stats.activity_series(db, GUILD_ID, period)
        assert sum(row[3] for row in rows) == 2
        assert sum(row[4] for row in rows) == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_membership_approval_candidate_and_server_counts(tmp_path):
    migrate(url(tmp_path))
    db = Database(url(tmp_path))
    now = int(datetime(2026, 11, 16, 16, tzinfo=timezone.utc).timestamp())
    try:
        async with db.session() as session:
            application = Application(
                guild_id=GUILD_ID, user_id=111, username="alpha", name="Alpha",
                gender="male", age=20, inviter="friend", status="approved",
                created_at=now - 100, decided_at=now - 50,
            )
            session.add(application)
            await session.flush()
            session.add(ApplicationEvent(
                application_id=application.id, actor_id=333, old_status="approving",
                new_status="approved", reason="Принят", created_at=now - 50,
            ))
        members = [
            SimpleNamespace(
                id=111, bot=False, roles=[SimpleNamespace(id=444)],
                joined_at=datetime.fromtimestamp(now - 200, tz=timezone.utc)),
            SimpleNamespace(
                id=222, bot=False, roles=[SimpleNamespace(id=555)], joined_at=None),
            SimpleNamespace(
                id=333, bot=False, roles=[], joined_at=None),
        ]
        period = stats.period_bounds("7d", timezone="UTC", now=now)
        overview = await stats.server_totals(
            db, guild_id=GUILD_ID, period=period,
            members=members, member_count=4, member_role_id=444,
            candidate_role_id=555, tracking_started_at=now - 75,
        )
        assert (overview.total, overview.approved, overview.candidates) == (4, 1, 1)
        assert (overview.approvals, overview.rejections) == (1, 0)
        assert await stats.approved_ids(db, GUILD_ID, members, 444) == {111}
        members[0].joined_at = datetime.fromtimestamp(now + 20, tz=timezone.utc)
        assert await stats.approved_ids(db, GUILD_ID, members, 444) == set()
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_user_privacy_wallet_rankings_and_panel_persistence(tmp_path):
    migrate(url(tmp_path))
    db = Database(url(tmp_path))
    now = int(datetime(2026, 11, 16, 16, tzinfo=timezone.utc).timestamp())
    try:
        await stats.record_activity(
            db, guild_id=GUILD_ID, user_id=10, kind="message",
            reference=400, timestamp=now, timezone="UTC",
        )
        await stats.record_activity(
            db, guild_id=GUILD_ID, user_id=20, kind="message",
            reference=401, timestamp=now, timezone="UTC",
        )
        async with db.session() as session:
            session.add(MemberProfile(
                guild_id=GUILD_ID, user_id=20, public=False, updated_at=now,
            ))
            session.add(Wallet(
                guild_id=GUILD_ID, user_id=20, balance=99, earned=99,
                spent=0, created_at=now, updated_at=now,
            ))
        public_view = await stats.personal_totals(
            db, guild_id=GUILD_ID, user_id=20, include_balance=False)
        private_view = await stats.personal_totals(
            db, guild_id=GUILD_ID, user_id=20, include_balance=True)
        assert not public_view.public
        assert public_view.wallet_balance is None
        assert private_view.wallet_balance == 99
        assert private_view.tracked_messages == 1
        period = stats.period_bounds("7d", timezone="UTC", now=now)
        leaders = await stats.leaderboard(
            db, guild_id=GUILD_ID, period=period,
            metric="messages", active_user_ids={10, 20})
        assert leaders == [(10, 1)]
        assert await stats.leaderboard(db, guild_id=GUILD_ID, period=period,
                                      metric="messages", active_user_ids=set()) == []
        await stats.save_panel(db, guild_id=GUILD_ID, channel_id=333,
                               message_id=444, kind="server", now=now)
        await stats.save_panel(db, guild_id=GUILD_ID, channel_id=333,
                               message_id=555, kind="server", now=now + 1)
        assert len(await stats.panels(db, GUILD_ID)) == 1
        p = (await stats.panels(db, GUILD_ID))[0]
        assert p.message_id == 555
    finally:
        await db.close()
    db = Database(url(tmp_path))
    try:
        p = (await stats.panels(db, GUILD_ID))[0]
        assert p.message_id == 555
        await stats.remove_panel(db, p.id, GUILD_ID)
        assert await stats.panels(db, GUILD_ID) == []
    finally:
        await db.close()
