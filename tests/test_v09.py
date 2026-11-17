"""v0.9.0: голосовые комнаты, ограничения доступа и сохранение состояния."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.config import GUILD_ID, VERSION
from app.core.catalog import implemented
from app.core.database import migrate


def test_voice_schema_and_unique_ownership(tmp_path):
    file = tmp_path / "voice.db"
    url = f"sqlite+aiosqlite:///{file}"
    migrate(url)
    migrate(url)
    with sqlite3.connect(file) as sql:
        sql.execute("PRAGMA foreign_keys=ON")
        assert VERSION == "1.5.0"
        assert implemented("voice")
        assert sql.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0014_community_categories"
        tables = {r[0] for r in sql.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"voice_rooms", "voice_room_access"} <= tables
        insert = ("INSERT INTO voice_rooms(guild_id,channel_id,owner_id,lobby_id,name,user_limit,locked,hidden,"
                  "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)")
        sql.execute(insert, (GUILD_ID, 123, 1, 345, "Первый", 0, 0, 0, 100, 100))
        with pytest.raises(sqlite3.IntegrityError):
            sql.execute(insert, (GUILD_ID, 124, 1, 345, "Дубль владельца", 0, 0, 0, 100, 100))
        with pytest.raises(sqlite3.IntegrityError):
            sql.execute(insert, (GUILD_ID, 123, 2, 345, "Дубль канала", 0, 0, 0, 100, 100))
        with pytest.raises(sqlite3.IntegrityError):
            sql.execute(insert, (GUILD_ID, 125, 2, 345, "Неверный лимит", 100, 0, 0, 100, 100))
        sql.execute("INSERT INTO voice_room_access(room_id,user_id,mode) VALUES (1,42,'allow')")
        with pytest.raises(sqlite3.IntegrityError):
            sql.execute("INSERT INTO voice_room_access(room_id,user_id,mode) VALUES (1,42,'deny')")
        with pytest.raises(sqlite3.IntegrityError):
            sql.execute("INSERT INTO voice_room_access(room_id,user_id,mode) VALUES (1,43,'other')")
        sql.execute("DELETE FROM voice_room_access WHERE room_id=1")
        sql.execute("DELETE FROM voice_rooms WHERE id=1")


def test_channel_select_and_intents_are_configured():
    root = Path(__file__).resolve().parents[1]
    bot = (root / "app/bot.py").read_text(encoding="utf-8")
    settings = (root / "app/panels/botsettings.py").read_text(encoding="utf-8")
    assert "intents.voice_states = True" in bot
    assert '("voice_lobby_channel_id", "Голосовой канал создания")' in settings
    assert '("voice_category_channel_id", "Категория голосовых комнат")' in settings
    assert 'discord.ChannelType.voice if self.voice_slot' in settings
    assert 'label="Голосовые комнаты"' in settings


def test_voice_room_name_validation():
    pytest.importorskip("discord")
    from app.modules.voice import room_name

    assert room_name("Комната друга") == "Комната друга"
    assert room_name(" А   Б ") == "А Б"
    with pytest.raises(ValueError):
        room_name(" ")
    with pytest.raises(ValueError):
        room_name("@everyone тест")
    with pytest.raises(ValueError):
        room_name("a" * 91)


def test_voice_discord_controls_and_commands():
    pytest.importorskip("discord")
    import discord

    from app.modules.voice import RoomControls, Voice
    from app.panels.botsettings import ChannelPicker

    assert ChannelPicker(None, 1, "voice_lobby_channel_id").channel_types == [discord.ChannelType.voice]
    assert ChannelPicker(None, 1, "voice_category_channel_id").channel_types == [discord.ChannelType.category]
    assert len(RoomControls(object(), 100).children) == 6
    commands = {c.name for c in Voice.voice_commands.commands}
    assert {"setup", "settings", "configure", "panel", "info", "name", "limit", "lock", "hide",
            "allow", "deny", "reset-access", "transfer", "claim", "close", "kick"} <= commands


@pytest.mark.asyncio
async def test_voice_room_service_lifecycle(tmp_path):
    pytest.importorskip("aiosqlite")
    from app.core.database import Database
    from app.services import voice

    url = f"sqlite+aiosqlite:///{tmp_path / 'rooms.db'}"
    migrate(url)
    db = Database(url)
    try:
        record = await voice.register(db, guild_id=GUILD_ID, channel_id=111, owner_id=15,
                                      lobby_id=10, name="Комната 15")
        assert (await voice.by_owner(db, GUILD_ID, 15)).channel_id == 111
        assert (await voice.by_channel(db, GUILD_ID, 111)).owner_id == 15
        with pytest.raises(ValueError):
            await voice.register(db, guild_id=GUILD_ID, channel_id=112, owner_id=15,
                                 lobby_id=10, name="Дубликат")
        with pytest.raises(ValueError):
            await voice.register(db, guild_id=GUILD_ID, channel_id=111, owner_id=16,
                                 lobby_id=10, name="Дубликат канала")
        await voice.set_access(db, record.id, 18, "allow")
        await voice.set_access(db, record.id, 18, "deny")
        assert await voice.access_mode(db, record.id, 18) == "deny"
        await voice.clear_access(db, record.id, 18)
        assert await voice.access_mode(db, record.id, 18) is None
        await voice.update_state(db, GUILD_ID, 111, locked=True, hidden=True, user_limit=7)
        await voice.rename(db, GUILD_ID, 111, name="Новая комната")
        updated = await voice.by_channel(db, GUILD_ID, 111)
        assert (updated.locked, updated.hidden, updated.user_limit, updated.name) == (True, True, 7, "Новая комната")
        await voice.transfer(db, GUILD_ID, 111, 19)
        assert await voice.by_owner(db, GUILD_ID, 15) is None
        assert (await voice.by_owner(db, GUILD_ID, 19)).channel_id == 111
        await voice.set_access(db, record.id, 20, "allow")
        await voice.remove(db, GUILD_ID, 111)
        assert await voice.by_channel(db, GUILD_ID, 111) is None
        assert await voice.access_entries(db, record.id) == []
        await voice.remove(db, GUILD_ID, 111)
    finally:
        await db.close()
