"""v0.8.0 — достижения, задания, периодизация и транзакционные награды."""
from __future__ import annotations

import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.config import GUILD_ID, VERSION
from app.core.database import migrate
from app.services import achievements, quests


def test_catalog_and_timezone_periods():
    assert VERSION == "0.11.1"
    assert len(quests.CATALOG) == 6
    assert len(achievements.CATALOG) == 8
    assert len(quests.BY_CODE) == len(quests.CATALOG)
    assert len(achievements.BY_CODE) == len(achievements.CATALOG)
    assert all(x.xp > 0 and x.coins > 0 for x in quests.CATALOG)
    assert all(x.xp > 0 and x.coins > 0 for x in achievements.CATALOG)
    assert any(x.hidden and x.rarity == "epic" for x in achievements.CATALOG)
    # День перехода на зимнее время длится 25 ч: период не равен фиксированным 86400 сек.
    now = int(datetime(2026, 10, 25, 12, tzinfo=ZoneInfo("Europe/Tallinn")).timestamp())
    key, start, end = quests.period_bounds(now, "Europe/Tallinn", "daily")
    assert key == "2026-10-25" and end - start == 25 * 3600
    week, start, end = quests.period_bounds(now, "Europe/Tallinn", "weekly")
    assert week == "2026-10-19" and end > start
    with pytest.raises(ValueError):
        quests.period_bounds(now, "UTC", "monthly")


def test_receipt_migration_and_uniqueness(tmp_path):
    dbfile = tmp_path / "quests.db"
    migrate(f"sqlite+aiosqlite:///{dbfile}")
    migrate(f"sqlite+aiosqlite:///{dbfile}")
    with sqlite3.connect(dbfile) as sql:
        sql.execute("PRAGMA foreign_keys=ON")
        assert sql.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0010_events"
        tables = {row[0] for row in sql.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"quest_claims", "achievement_unlocks"} <= tables
        row = (GUILD_ID, 101, "daily_talk", "2026-11-16", "daily", 35, 25, 100)
        sql.execute("INSERT INTO quest_claims(guild_id,user_id,code,period,frequency,xp_reward,coin_reward,claimed_at) "
                    "VALUES (?,?,?,?,?,?,?,?)", row)
        with pytest.raises(sqlite3.IntegrityError):
            sql.execute("INSERT INTO quest_claims(guild_id,user_id,code,period,frequency,xp_reward,coin_reward,claimed_at) "
                        "VALUES (?,?,?,?,?,?,?,?)", row)
        sql.execute("INSERT INTO achievement_unlocks(guild_id,user_id,code,unlocked_at) VALUES (?,?,?,?)",
                    (GUILD_ID, 101, "first_words", 100))
        with pytest.raises(sqlite3.IntegrityError):
            sql.execute("INSERT INTO achievement_unlocks(guild_id,user_id,code,unlocked_at) VALUES (?,?,?,?)",
                        (GUILD_ID, 101, "first_words", 101))


@pytest.mark.asyncio
async def test_daily_quest_and_achievement_rewards_idempotent(tmp_path):
    pytest.importorskip("aiosqlite")
    from app.core.database import Database
    from app.services import economy, progression

    url = f"sqlite+aiosqlite:///{tmp_path / 'rewards.db'}"
    migrate(url)
    db = Database(url)
    try:
        now = int(datetime(2026, 11, 16, 12, tzinfo=ZoneInfo("UTC")).timestamp())
        for i in range(3):
            r = await progression.award_message(db, guild_id=GUILD_ID, user_id=101,
                message_id=1000+i, content=f"Это полезное сообщение номер {i} для NewSquad",
                now=now+i*80, timezone="UTC")
            assert r.amount == 20
        states = await quests.list_quests(db, GUILD_ID, 101, "UTC", "daily", now=now+240)
        d = next(s for s in states if s.quest.code == "daily_talk")
        assert d.value == 3 and not d.claimed
        award = await quests.claim(db, guild_id=GUILD_ID, user_id=101,
                                   code="daily_talk", timezone="UTC", now=now+240)
        assert (award.xp, award.coins) == (35, 25)
        with pytest.raises(ValueError, match="уже получена"):
            await quests.claim(db, guild_id=GUILD_ID, user_id=101,
                               code="daily_talk", timezone="UTC", now=now+240)
        assert (await economy.wallet(db, GUILD_ID, 101)).balance == 25
        assert (await progression.get_progress(db, GUILD_ID, 101)).xp == 95
        assert (await achievements.sync(db, GUILD_ID, 101, now=now+240)) == ["first_words"]
        assert await achievements.sync(db, GUILD_ID, 101, now=now+241) == []
        ach = await achievements.claim(db, guild_id=GUILD_ID, user_id=101,
                                       code="first_words", timezone="UTC", now=now+250)
        assert (ach.xp, ach.coins) == (15, 20)
        with pytest.raises(ValueError, match="уже получена"):
            await achievements.claim(db, guild_id=GUILD_ID, user_id=101,
                                     code="first_words", timezone="UTC", now=now+250)
        assert (await economy.wallet(db, GUILD_ID, 101)).balance == 45
        assert (await progression.get_progress(db, GUILD_ID, 101)).xp == 110
        tomorrow = now + 86400
        future = await quests.list_quests(db, GUILD_ID, 101, "UTC", "daily", now=tomorrow)
        assert not any(s.claimed for s in future)
        assert all(s.value == 0 for s in future)
        # Достижения, в отличие от заданий, не сбрасываются на следующий день.
        assert next(s for s in await achievements.list_achievements(db, GUILD_ID, 101)
                    if s.achievement.code == "first_words").claimed_at == now+250
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_failed_coin_payout_rolls_back_quest_and_xp(tmp_path):
    pytest.importorskip("aiosqlite")
    from sqlalchemy import select, update

    from app.core.database import Database
    from app.models import QuestClaim, Wallet
    from app.services import economy, progression

    url = f"sqlite+aiosqlite:///{tmp_path / 'rollback.db'}"
    migrate(url)
    db = Database(url)
    now = int(datetime(2026, 11, 16, 12, tzinfo=ZoneInfo("UTC")).timestamp())
    try:
        for i in range(3):
            await progression.award_message(db, guild_id=GUILD_ID, user_id=4,
                message_id=2000+i, content=f"Интересное сообщение для друзей номер {i}",
                now=now+i*80, timezone="UTC")
        await economy.credit_once(db, guild_id=GUILD_ID, user_id=4, amount=100,
                                  kind="test", reference="seed")
        async with db.session() as session:
            await session.execute(update(Wallet).where(Wallet.guild_id == GUILD_ID,
                Wallet.user_id == 4).values(balance=economy.MAX_BALANCE))
        before = (await progression.get_progress(db, GUILD_ID, 4)).xp
        with pytest.raises(ValueError, match="лимит кошелька"):
            await quests.claim(db, guild_id=GUILD_ID, user_id=4,
                               code="daily_talk", timezone="UTC", now=now+300)
        async with db.sessions() as session:
            assert await session.scalar(select(QuestClaim).where(QuestClaim.user_id == 4)) is None
        assert (await progression.get_progress(db, GUILD_ID, 4)).xp == before
        async with db.session() as session:
            await session.execute(update(Wallet).where(Wallet.guild_id == GUILD_ID,
                Wallet.user_id == 4).values(balance=100))
        earned = await quests.claim(db, guild_id=GUILD_ID, user_id=4,
                                   code="daily_talk", timezone="UTC", now=now+300)
        assert earned.coins == 25
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_weekly_daily_claims_do_not_duplicate(tmp_path):
    pytest.importorskip("aiosqlite")
    from app.core.database import Database
    from app.services import progression

    url = f"sqlite+aiosqlite:///{tmp_path / 'weekly.db'}"
    migrate(url)
    db = Database(url)
    try:
        start = int(datetime(2026, 11, 16, 11, tzinfo=ZoneInfo("UTC")).timestamp())
        for day in range(4):
            at = start + day*86400
            for i in range(3):
                await progression.award_message(db, guild_id=GUILD_ID, user_id=7,
                    message_id=5000+day*10+i,
                    content=f"Всем привет, будем играть сегодня {day}, сообщение {i}",
                    now=at+80*i, timezone="UTC")
            await quests.claim(db, guild_id=GUILD_ID, user_id=7,
                               code="daily_talk", timezone="UTC", now=at+250)
        weekly = await quests.list_quests(db, GUILD_ID, 7, "UTC", "weekly", now=start+3*86400+300)
        target = next(s for s in weekly if s.quest.code == "weekly_daily")
        assert target.value == 4
        await quests.claim(db, guild_id=GUILD_ID, user_id=7, code="weekly_daily",
                           timezone="UTC", now=start+3*86400+300)
        with pytest.raises(ValueError):
            await quests.claim(db, guild_id=GUILD_ID, user_id=7, code="weekly_daily",
                               timezone="UTC", now=start+3*86400+300)
    finally:
        await db.close()
