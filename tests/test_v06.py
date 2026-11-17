"""v0.6.0: уровни, защита от повторных начислений и личные профили."""
from __future__ import annotations

import sqlite3

import pytest

from app.config import GUILD_ID, VERSION
from app.core.database import migrate
from app.services import profiles, progression


def test_version_and_thresholds():
    assert VERSION == "1.3.0"
    assert [progression.threshold(level) for level in range(1, 5)] == [0, 200, 600, 1200]
    for xp, level in [(0, 1), (199, 1), (200, 2), (599, 2), (600, 3), (1199, 3), (1200, 4)]:
        assert progression.level_for_xp(xp) == level
    assert len(progression.progress_bar(200)) == 12
    with pytest.raises(ValueError):
        progression.level_for_xp(-1)


def test_text_antispam_and_profile_validation():
    assert not progression.valid_message("Тест")
    assert not progression.valid_message("https://example.com")
    assert progression.valid_message("Отличная игра, всем привет!")
    assert progression.normalize_message("  ПРИВЕТ   Мир ") == "привет мир"
    assert profiles.clean("bio", "  Игрок\n  NewSquad  ") == "Игрок NewSquad"
    assert profiles.clean("availability", "looking") == "looking"
    with pytest.raises(ValueError):
        profiles.clean("availability", "invalid")
    with pytest.raises(ValueError):
        profiles.clean("bio", "x" * 501)


def test_v06_migration_idempotent(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'v06.db'}"
    migrate(url)
    migrate(url)
    with sqlite3.connect(tmp_path / "v06.db") as db:
        db.execute("PRAGMA foreign_keys=ON")
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"member_progress", "member_profiles", "experience_awards"} <= tables
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0013_announcements"
        db.execute("INSERT INTO member_progress(guild_id,user_id,created_at,updated_at) VALUES (?,?,?,?)",
                   (GUILD_ID, 111, 100, 100))
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO member_progress(guild_id,user_id,created_at,updated_at) VALUES (?,?,?,?)",
                       (GUILD_ID, 111, 100, 100))
        db.execute("INSERT INTO experience_awards(guild_id,user_id,source,reference,created_at) VALUES (?,?,?,?,?)",
                   (GUILD_ID, 111, "message", "123456", 100))
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO experience_awards(guild_id,user_id,source,reference,created_at) VALUES (?,?,?,?,?)",
                       (GUILD_ID, 111, "message", "123456", 100))


@pytest.mark.asyncio
async def test_message_receipts_cooldown_cap_and_level_outbox(tmp_path):
    pytest.importorskip("aiosqlite")
    from sqlalchemy import select

    from app.core.database import Database
    from app.models import OutboxEvent

    dbpath = tmp_path / "xp.db"
    url = f"sqlite+aiosqlite:///{dbpath}"
    migrate(url)
    db = Database(url)
    try:
        payload = dict(guild_id=GUILD_ID, user_id=1, content="Всем привет, поиграем в Deadlock?",
                       now=1_700_000_000, amount=100, cooldown=60, daily_cap=250)
        first = await progression.award_message(db, message_id=1001, **payload)
        assert first.amount == 100 and first.level_after == 1
        duplicate = await progression.award_message(db, message_id=1001, **{**payload, "now": payload["now"] + 1000})
        assert duplicate.amount == 0
        blocked = await progression.award_message(db, message_id=1002, **{**payload, "now": payload["now"] + 20})
        assert blocked.amount == 0
        repeated = await progression.award_message(db, message_id=1003, **{**payload, "now": payload["now"] + 100})
        assert repeated.amount == 0  # identical content even after cooldown
        second = await progression.award_message(db, message_id=1004, **{**payload,
                       "now": payload["now"] + 200, "content": "Ищем людей в команду на вечер"})
        assert second.amount == 100 and second.level_after == 2
        third = await progression.award_message(db, message_id=1005, **{**payload,
                       "now": payload["now"] + 300, "content": "Сегодня будет веселая катка"})
        assert third.amount == 50
        row = await progression.get_progress(db, GUILD_ID, 1)
        assert row.xp == 250 and row.level == 2 and row.credited_messages == 3
        async with db.sessions() as session:
            events = (await session.scalars(select(OutboxEvent))).all()
        assert sum(e.kind == "leveling.level_up" for e in events) == 1
        assert sum(e.kind == "leveling.xp_awarded" for e in events) == 3
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_voice_minutes_survive_restart_and_profile_privacy(tmp_path):
    pytest.importorskip("aiosqlite")
    from app.core.database import Database

    url = f"sqlite+aiosqlite:///{tmp_path / 'voice.db'}"
    migrate(url)
    db = Database(url)
    try:
        for minute in range(10, 20):
            result = await progression.record_voice_minute(db, guild_id=GUILD_ID, user_id=2,
                                                            minute=minute, reward_xp=30)
        assert result.amount == 30
        assert (await progression.record_voice_minute(db, guild_id=GUILD_ID, user_id=2,
                                                      minute=19)).amount == 0
        assert (await progression.get_progress(db, GUILD_ID, 2)).active_voice_minutes == 10
        await profiles.update_profile(db, GUILD_ID, 2, "bio", "Люблю игры и общение")
        await profiles.update_profile(db, GUILD_ID, 2, "availability", "looking")
        await profiles.set_visibility(db, GUILD_ID, 2, public=False)
        profile = await profiles.get_profile(db, GUILD_ID, 2)
        assert profile.bio == "Люблю игры и общение" and profile.availability == "looking" and not profile.public
        await profiles.reset_profile(db, GUILD_ID, 2)
        assert await profiles.get_profile(db, GUILD_ID, 2) is None
        assert (await progression.get_progress(db, GUILD_ID, 2)).xp == 30
    finally:
        await db.close()


def test_new_cogs_exposed():
    discord = pytest.importorskip("discord")
    from app.modules.leveling import Leveling
    from app.modules.profiles import Profiles
    from app.panels.botsettings import HomeView
    assert Leveling.levels.name == "levels"
    assert Profiles.profile.name == "profile"
    class Stub:
        config = type("Config", (), {"owner_ids": frozenset()})()
    view = HomeView(Stub(), 1)
    names = {b.label for b in view.children if isinstance(b, discord.ui.Button)}
    assert {"Уровни и XP", "Профили", "Все настройки"} <= names
