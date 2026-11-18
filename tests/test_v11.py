"""v0.11.1: мероприятия, очередь ожидания, розыгрыши, миграция и идемпотентность."""
from __future__ import annotations

import asyncio
import json
import random
import sqlite3
import time
from datetime import datetime

import discord
import pytest

from app.config import GUILD_ID, VERSION
from app.core.catalog import implemented
from app.core.database import Database, migrate
from app.modules.events import Events, EventView, GiveawayView
from app.panels.botsettings import HomeView
from app.services import events as store


def db_url(tmp_path, name="events.db"):
    return f"sqlite+aiosqlite:///{tmp_path / name}"


def test_events_migration_and_constraints(tmp_path):
    url = db_url(tmp_path)
    migrate(url)
    migrate(url)
    assert VERSION == "1.8.0"
    assert implemented("events")
    with sqlite3.connect(tmp_path / "events.db") as con:
        con.execute("PRAGMA foreign_keys=ON")
        assert con.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0015_support_live_ops"
        tables = {x[0] for x in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"community_events", "community_event_signups", "community_event_reminders",
                "community_giveaways", "community_giveaway_entries", "community_giveaway_draws"} <= tables


def test_timezone_and_reminders():
    from zoneinfo import ZoneInfo

    ts = store.local_to_utc("2026-11-20 19:30", "Europe/Tallinn")
    assert datetime.fromtimestamp(ts, ZoneInfo("Europe/Tallinn")).strftime("%Y-%m-%d %H:%M") == "2026-11-20 19:30"
    assert store.reminder_offsets("15, 60") == (60, 15)
    assert store.reminder_offsets("") == ()
    with pytest.raises(ValueError):
        store.reminder_offsets("15,15")
    with pytest.raises(ValueError):
        store.reminder_offsets("5,10,15,20,25")
    with pytest.raises(ValueError):
        store.local_to_utc("2026-03-29 03:30", "Europe/Tallinn")
    with pytest.raises(ValueError):
        store.local_to_utc("2026-10-25 03:30", "Europe/Tallinn")
    with pytest.raises(ValueError):
        store.local_to_utc("2026-11-20 19:30", "Fake/TZ")


@pytest.mark.asyncio
async def test_event_signup_waitlist_promotion_rejoin_and_lifecycle(tmp_path):
    url = db_url(tmp_path)
    migrate(url)
    db = Database(url)
    now = int(time.time())
    try:
        event = await store.create_event(db, guild_id=GUILD_ID, creator_id=5, host_id=8,
            channel_id=120, title="Совместная игра", description="Вечер с участниками",
            timezone_name="Europe/Tallinn", starts_at=now+3600, duration_minutes=90,
            capacity=2, waitlist_enabled=True, reminders="60,15", now=now)
        assert event.status == "scheduled"
        assert await store.register(db, GUILD_ID, event.id, 10, now=now) == "registered"
        assert await store.register(db, GUILD_ID, event.id, 11, now=now+1) == "registered"
        assert await store.register(db, GUILD_ID, event.id, 12, now=now+2) == "waitlist"
        assert await store.register(db, GUILD_ID, event.id, 13, now=now+3) == "waitlist"
        with pytest.raises(ValueError, match="уже"):
            await store.register(db, GUILD_ID, event.id, 10, now=now+5)
        assert (await store.get_event(db, GUILD_ID, event.id)).confirmed_count == 2
        assert await store.withdraw(db, GUILD_ID, event.id, 11, now=now+6) == 12
        assert [(r.user_id, r.status) for r in await store.event_signups(db, event.id)] == [
            (10, "registered"), (11, "cancelled"), (12, "registered"), (13, "waitlist")]
        event = await store.get_event(db, GUILD_ID, event.id)
        assert (event.confirmed_count, event.waitlist_count) == (2, 1)
        assert await store.register(db, GUILD_ID, event.id, 11, now=now+7) == "waitlist"
        assert await store.withdraw(db, GUILD_ID, event.id, 10, now=now+9) == 13
        assert await store.reminder_state(db, GUILD_ID, event.id, 60) is not None
        assert await store.mark_reminder_sent(db, event.id, 60, now=now+10)
        assert not await store.mark_reminder_sent(db, event.id, 60, now=now+11)
        assert not await store.mark_live(db, GUILD_ID, event.id, now=now+100)
        assert await store.mark_live(db, GUILD_ID, event.id, now=now+3600)
        assert not await store.mark_live(db, GUILD_ID, event.id, now=now+3601)
        with pytest.raises(ValueError):
            await store.register(db, GUILD_ID, event.id, 99, now=now+3601)
        assert await store.mark_finished(db, GUILD_ID, event.id, now=now+3600+90*60)
        assert not await store.mark_finished(db, GUILD_ID, event.id, now=now+3600+90*60)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_event_concurrent_one_seat_and_no_waitlist(tmp_path):
    url = db_url(tmp_path)
    migrate(url)
    db = Database(url)
    now = int(time.time())
    try:
        e = await store.create_event(db, guild_id=GUILD_ID, creator_id=1, host_id=1,
            channel_id=2, title="Тест мест", description="Участники", timezone_name="UTC",
            starts_at=now+7200, capacity=1, waitlist_enabled=False, now=now)
        result = await asyncio.gather(*(
            store.register(db, GUILD_ID, e.id, uid, now=now+1) for uid in range(10, 15)
        ), return_exceptions=True)
        assert len([x for x in result if x == "registered"]) == 1, result
        assert len([x for x in result if isinstance(x, ValueError)]) == 4, result
        assert (await store.get_event(db, GUILD_ID, e.id)).confirmed_count == 1
        assert len(await store.event_signups(db, e.id)) == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_confirm_attendance_is_atomic_idempotent(tmp_path):
    url = db_url(tmp_path)
    migrate(url)
    db = Database(url)
    now = int(time.time())
    try:
        event = await store.create_event(db, guild_id=GUILD_ID, creator_id=1, host_id=1,
            channel_id=2, title="Награды", description="Участие подтверждается ведущим",
            timezone_name="UTC", starts_at=now+3600, duration_minutes=30,
            reward_xp=50, reward_coins=40, now=now)
        await store.register(db, GUILD_ID, event.id, 88, now=now+3)
        await store.mark_live(db, GUILD_ID, event.id, now=now+3600)
        assert await store.confirm_attendance(db, guild_id=GUILD_ID,
            event_id=event.id, user_id=88, actor_id=9, timezone_name="UTC",
            now=now+3601) == (50, 40)
        with pytest.raises(ValueError, match="уже"):
            await store.confirm_attendance(db, guild_id=GUILD_ID,
                event_id=event.id, user_id=88, actor_id=9,
                timezone_name="UTC", now=now+3602)
        from app.services import economy, progression

        assert (await economy.wallet(db, GUILD_ID, 88)).balance == 40
        assert (await progression.get_progress(db, GUILD_ID, 88)).xp == 50
        with sqlite3.connect(tmp_path / "events.db") as sql:
            count = sql.execute("SELECT COUNT(*) FROM experience_awards WHERE source='event'").fetchone()[0]
            assert count == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_giveaway_reentrance_and_draw_history(tmp_path):
    url = db_url(tmp_path)
    migrate(url)
    db = Database(url)
    now = int(time.time())
    try:
        g = await store.create_giveaway(db, guild_id=GUILD_ID, creator_id=1,
            channel_id=2, prize="Декоративная роль", conditions="Бесплатно",
            ends_at=now+3600, winner_count=2, min_level=1, now=now)
        for uid in range(10, 15):
            await store.enter_giveaway(db, GUILD_ID, g.id, uid, now=now+1)
        with pytest.raises(ValueError, match="уже"):
            await store.enter_giveaway(db, GUILD_ID, g.id, 10, now=now+2)
        assert (await store.get_giveaway(db, GUILD_ID, g.id)).entry_count == 5
        with pytest.raises(ValueError, match="ещё"):
            await store.draw_giveaway(db, guild_id=GUILD_ID, giveaway_id=g.id,
                eligible_ids=[10, 11, 12, 13, 14], actor_id=0, reason="По таймеру", now=now+2)
        winners = await store.draw_giveaway(db, guild_id=GUILD_ID, giveaway_id=g.id,
            eligible_ids=[10, 11, 12, 14, 100000], actor_id=0, reason="По таймеру",
            now=now+3600, rng=random.Random(3))
        assert len(winners) == len(set(winners)) == 2
        assert set(winners) <= {10, 11, 12, 14}
        with pytest.raises(ValueError, match="уже"):
            await store.draw_giveaway(db, guild_id=GUILD_ID, giveaway_id=g.id,
                eligible_ids=[10, 11, 12, 14], actor_id=0, reason="Ещё раз", force=True, now=now+3600)
        new = await store.reroll_giveaway(db, guild_id=GUILD_ID, giveaway_id=g.id,
            actor_id=500, reason="Победитель отказался от приза", now=now+3700, rng=random.Random(7))
        assert len(new) == 2 and not (set(new) & set(winners))
        history = await store.giveaway_draws(db, g.id)
        assert [x.round for x in history] == [1, 2]
        assert history[0].reason == "По таймеру"
        assert history[1].actor_id == 500
        assert json.loads(history[0].eligible_json) == [10, 11, 12, 14]
        assert json.loads(history[0].winners_json) == winners
        assert (await store.get_giveaway(db, GUILD_ID, g.id)).winners_json == json.dumps(new)
        with pytest.raises(ValueError, match="Нет новых"):
            await store.reroll_giveaway(db, guild_id=GUILD_ID, giveaway_id=g.id,
                actor_id=500, reason="Ещё выбор", now=now+3800)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_giveaway_restart_persists_entries_and_no_paid_operations(tmp_path):
    url = db_url(tmp_path)
    migrate(url)
    db = Database(url)
    now = int(time.time())
    try:
        g = await store.create_giveaway(db, guild_id=GUILD_ID, creator_id=1,
            channel_id=2, prize="Значок", conditions="Бесплатный розыгрыш",
            ends_at=now+3600, now=now)
        await store.enter_giveaway(db, GUILD_ID, g.id, 100, now=now+1)
    finally:
        await db.close()
    db = Database(url)
    try:
        assert await store.giveaway_entrants(db, g.id) == [100]
        await store.draw_giveaway(db, guild_id=GUILD_ID, giveaway_id=g.id,
            eligible_ids=[100], actor_id=0, reason="Итог", now=now+3601)
        assert (await store.get_giveaway(db, GUILD_ID, g.id)).draw_round == 1
        with pytest.raises(ValueError):
            await store.enter_giveaway(db, GUILD_ID, g.id, 101, now=now+3601)
    finally:
        await db.close()


def test_discord_views_and_commands_exist():
    event_view = EventView(object(), 12)
    draw_view = GiveawayView(object(), 7)
    assert event_view.timeout is None and draw_view.timeout is None
    assert len(event_view.children) == 3 and len(draw_view.children) == 1
    assert {c.custom_id for c in event_view.children} == {
        "newsqad:event:12:join", "newsqad:event:12:leave", "newsqad:event:12:members"}
    assert draw_view.children[0].custom_id == "newsqad:giveaway:7:join"
    assert {c.name for c in Events.event.commands} >= {
        "create", "list", "join", "leave", "participants", "cancel", "confirm", "settings", "configure"}
    assert {c.name for c in Events.giveaway.commands} >= {
        "create", "join", "list", "end", "reroll", "history"}
    class Stub:
        config = type("Config", (), {"owner_ids": frozenset()})()

    view = HomeView(Stub(), 1)
    assert "Мероприятия" in {option.label for item in view.children if isinstance(item, discord.ui.Select)
                                for option in item.options}


@pytest.mark.asyncio
async def test_event_completion_and_cancellation_have_single_durable_notice(tmp_path):
    url = db_url(tmp_path, "notices.db")
    migrate(url)
    db = Database(url)
    now = int(time.time())
    try:
        completed = await store.create_event(db, guild_id=GUILD_ID, creator_id=3,
            host_id=3, channel_id=4, title="Первое", description="Проверка",
            timezone_name="UTC", starts_at=now+3600, now=now)
        cancelled = await store.create_event(db, guild_id=GUILD_ID, creator_id=3,
            host_id=3, channel_id=4, title="Второе", description="Проверка",
            timezone_name="UTC", starts_at=now+7200, now=now)
        assert await store.mark_finished(db, GUILD_ID, completed.id, now=now+12000)
        assert not await store.mark_finished(db, GUILD_ID, completed.id, now=now+12001)
        assert await store.cancel_event(db, GUILD_ID, cancelled.id, reason="Нет ведущего", now=now)
        assert not await store.cancel_event(db, GUILD_ID, cancelled.id, reason="Повтор", now=now)
        with sqlite3.connect(tmp_path / "notices.db") as con:
            results = con.execute("SELECT id, kind, payload_json FROM outbox_events WHERE kind LIKE 'events.%'").fetchall()
            assert len(results) == 2
            assert {row[1] for row in results} == {"events.finished", "events.cancelled"}
            assert any(json.loads(row[2]).get("reason") == "Нет ведущего" for row in results)
    finally:
        await db.close()
