"""v0.12.0: Suggestions, транзакционные голоса, статусы и восстановление."""
from __future__ import annotations

import asyncio
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from app.config import GUILD_ID, VERSION
from app.core.catalog import implemented
from app.core.database import Database, migrate
from app.modules.suggestions import (
    CreatePanel,
    Suggestions,
    SuggestionView,
    suggestion_embed,
)
from app.services import suggestions as store


def database_url(tmp_path, name="suggestions.db"):
    return f"sqlite+aiosqlite:///{tmp_path / name}"


def test_v12_schema_and_discord_components(tmp_path):
    url = database_url(tmp_path)
    migrate(url)
    migrate(url)
    assert VERSION == "1.3.0"
    assert implemented("suggestions")
    with sqlite3.connect(tmp_path / "suggestions.db") as conn:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0013_announcements"
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert {
            "community_suggestions", "community_suggestion_votes",
            "community_suggestion_history", "community_suggestion_throttle",
            "community_suggestion_panels",
        } <= tables
    panel = CreatePanel(object())
    assert panel.timeout is None
    assert panel.children[0].custom_id == "newsqad:suggest:create"
    view = SuggestionView(object(), 123)
    assert view.timeout is None
    assert [c.custom_id for c in view.children] == [
        "newsqad:suggest:123:up", "newsqad:suggest:123:down",
        "newsqad:suggest:123:discuss",
    ]
    closed = SuggestionView(object(), 123, closed=True)
    assert [x.disabled for x in closed.children] == [True, True, False]
    names = {x.name for x in Suggestions.suggest.commands}
    assert {"create", "list", "view", "status", "settings",
            "configure", "panel", "restore"} <= names


@pytest.mark.asyncio
async def test_create_and_atomic_cooldown(tmp_path):
    url = database_url(tmp_path)
    migrate(url)
    db = Database(url)
    try:
        base = 1_800_000_000
        first = await store.create(
            db, guild_id=GUILD_ID, author_id=42, channel_id=54,
            topic="Новые турниры", description="Проводить командный турнир еженедельно.",
            cooldown=3600, now=base,
        )
        assert first.status == "new" and first.votes_for == 0
        assert (await store.get(db, 999, first.id)) is None
        with pytest.raises(ValueError, match="задержка"):
            await store.create(
                db, guild_id=GUILD_ID, author_id=42, channel_id=54,
                topic="Ещё одна идея", description="Проводить совместные игры.",
                cooldown=3600, now=base + 1,
            )
        again = await store.create(
            db, guild_id=GUILD_ID, author_id=42, channel_id=54,
            topic="Ещё одна идея", description="Проводить совместные игры.",
            cooldown=3600, now=base + 3600,
        )
        assert again.id > first.id
        results = await asyncio.gather(*(
            store.create(
                db, guild_id=GUILD_ID, author_id=55, channel_id=54,
                topic="Короткая идея", description="Предлагаю новый формат игр.",
                cooldown=300, now=base + 5000,
            ) for _ in range(5)
        ), return_exceptions=True)
        assert sum(isinstance(x, ValueError) for x in results) == 4, results
        assert sum(isinstance(x, store.Suggestion) for x in results) == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_vote_change_duplicate_and_final_state(tmp_path):
    url = database_url(tmp_path)
    migrate(url)
    db = Database(url)
    try:
        item = await store.create(
            db, guild_id=GUILD_ID, author_id=10, channel_id=30,
            topic="Удобные роли", description="Добавить игровые роли в меню.",
            now=1_800_000_000,
        )
        changed, row = await store.vote(
            db, guild_id=GUILD_ID, suggestion_id=item.id,
            user_id=1, choice=1,
        )
        assert changed and (row.votes_for, row.votes_against) == (1, 0)
        changed, row = await store.vote(
            db, guild_id=GUILD_ID, suggestion_id=item.id,
            user_id=1, choice=1,
        )
        assert not changed and (row.votes_for, row.votes_against) == (1, 0)
        changed, row = await store.vote(
            db, guild_id=GUILD_ID, suggestion_id=item.id,
            user_id=1, choice=-1,
        )
        assert changed and (row.votes_for, row.votes_against) == (0, 1)
        await store.vote(db, guild_id=GUILD_ID, suggestion_id=item.id,
                         user_id=2, choice=1)
        row = await store.get(db, GUILD_ID, item.id)
        assert (row.votes_for, row.votes_against) == (1, 1)
        with pytest.raises(ValueError):
            await store.vote(db, guild_id=GUILD_ID, suggestion_id=item.id,
                             user_id=5, choice=0)
        await store.change_status(
            db, guild_id=GUILD_ID, suggestion_id=item.id,
            actor_id=99, status="accepted", reason="Поддерживаем идею",
        )
        with pytest.raises(ValueError, match="закрыто"):
            await store.vote(db, guild_id=GUILD_ID, suggestion_id=item.id,
                             user_id=4, choice=1)
        with sqlite3.connect(tmp_path / "suggestions.db") as conn:
            assert conn.execute(
                "SELECT count(*) FROM community_suggestion_votes WHERE suggestion_id=?",
                (item.id,),
            ).fetchone()[0] == 2
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_concurrent_votes_have_exact_totals(tmp_path):
    url = database_url(tmp_path)
    migrate(url)
    db = Database(url)
    try:
        item = await store.create(
            db, guild_id=GUILD_ID, author_id=10, channel_id=30,
            topic="Больше игр", description="Список еженедельных мероприятий.",
            now=1_800_000_000,
        )
        await asyncio.gather(*(
            store.vote(
                db, guild_id=GUILD_ID, suggestion_id=item.id,
                user_id=uid, choice=1 if uid % 2 else -1,
            )
            for uid in range(10, 20)
        ))
        row = await store.get(db, GUILD_ID, item.id)
        assert (row.votes_for, row.votes_against) == (5, 5)
        await asyncio.gather(*(
            store.vote(
                db, guild_id=GUILD_ID, suggestion_id=item.id,
                user_id=uid, choice=1,
            )
            for uid in range(10, 20)
        ))
        row = await store.get(db, GUILD_ID, item.id)
        assert (row.votes_for, row.votes_against) == (10, 0)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_status_history_audit_survives_restart(tmp_path):
    url = database_url(tmp_path)
    migrate(url)
    db = Database(url)
    try:
        item = await store.create(
            db, guild_id=GUILD_ID, author_id=50, channel_id=60,
            topic="Изменение правил", description="Опубликовать новые правила турнира.",
        )
        await store.change_status(db, guild_id=GUILD_ID, suggestion_id=item.id,
                                  actor_id=9, status="under_review", reason="Проверим детали")
        await store.change_status(db, guild_id=GUILD_ID, suggestion_id=item.id,
                                  actor_id=9, status="accepted", reason="Принято в работу")
        await store.change_status(db, guild_id=GUILD_ID, suggestion_id=item.id,
                                  actor_id=9, status="implemented", reason="Введено в работу")
        with pytest.raises(ValueError):
            await store.change_status(db, guild_id=GUILD_ID, suggestion_id=item.id,
                                      actor_id=9, status="implemented", reason="Повтор")
        with pytest.raises(ValueError):
            await store.change_status(db, guild_id=GUILD_ID, suggestion_id=item.id,
                                      actor_id=9, status="bogus", reason="Ошибка")
        events = await store.history(db, GUILD_ID, item.id)
        assert [x.new_status for x in events] == [
            "new", "under_review", "accepted", "implemented",
        ]
        assert events[-1].reason == "Введено в работу"
        with sqlite3.connect(tmp_path / "suggestions.db") as sql:
            assert sql.execute(
                "SELECT count(*) FROM outbox_events WHERE kind='suggestions.status_changed'"
            ).fetchone()[0] == 3
    finally:
        await db.close()
    db = Database(url)
    try:
        assert (await store.get(db, GUILD_ID, item.id)).status == "implemented"
        assert len(await store.history(db, GUILD_ID, item.id)) == 4
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_panel_delete_warning_and_restore_without_losing_votes(tmp_path):
    url = database_url(tmp_path)
    migrate(url)
    db = Database(url)
    try:
        item = await store.create(
            db, guild_id=GUILD_ID, author_id=10, channel_id=30,
            topic="Новые роли", description="Сделать роли на новые игры.",
        )
        await store.vote(db, guild_id=GUILD_ID, suggestion_id=item.id,
                         user_id=25, choice=1)
        assert await store.set_message(db, GUILD_ID, item.id, 9876)
        assert not await store.set_message(db, GUILD_ID, item.id, 9877)
        await store.register_panel(db, GUILD_ID, 30, 9000)
        assert await store.deleted_panel(db, GUILD_ID, 30, 9876) == "suggestion"
        assert await store.deleted_panel(db, GUILD_ID, 30, 9876) is None
        assert await store.deleted_panel(db, GUILD_ID, 30, 9000) == "create_panel"
        assert await store.deleted_panel(db, GUILD_ID, 30, 9000) is None
        assert await store.missing_count(db, GUILD_ID) == (1, 1)
        assert (await store.get(db, GUILD_ID, item.id)).votes_for == 1
        assert await store.set_message(db, GUILD_ID, item.id, 9999)
        assert await store.deleted_panel(db, GUILD_ID, 30, 9876) is None
        assert await store.missing_count(db, GUILD_ID) == (0, 1)
        with sqlite3.connect(tmp_path / "suggestions.db") as conn:
            rows = conn.execute(
                "SELECT kind, payload_json FROM outbox_events WHERE kind='suggestions.panel_deleted'"
            ).fetchall()
        assert len(rows) == 2
        assert all("message_id" in value for _, value in rows)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_publish_uses_persistent_buttons_and_safe_mentions(monkeypatch):
    from app.services import suggestions as svc

    item = SimpleNamespace(
        id=7, guild_id=GUILD_ID, author_id=10, topic="Мой @everyone проект",
        description="Исправить настройки @here и добавить роли.",
        status="new", status_reason="", channel_id=44, message_id=None,
        thread_id=None, votes_for=0, votes_against=0, panel_missing=False,
    )
    fake_message = SimpleNamespace(id=12345)
    channel = MagicMock(spec=discord.TextChannel)
    channel.send = AsyncMock(return_value=fake_message)
    guild = SimpleNamespace(get_channel=lambda _cid: channel)
    bot = SimpleNamespace(
        config=SimpleNamespace(guild_id=GUILD_ID, owner_ids=frozenset()),
        database=object(), get_guild=lambda _id: guild, add_view=MagicMock(),
    )
    cog = Suggestions(bot)
    monkeypatch.setattr(svc, "set_message", AsyncMock(return_value=True))
    assert await cog._publish(item)
    kwargs = channel.send.await_args.kwargs
    assert kwargs["allowed_mentions"].everyone is False
    assert kwargs["embed"].color.value == 0x4A86F7
    assert "@everyone" not in kwargs["embed"].description
    bot.add_view.assert_called_once()


def test_input_safety_and_rendering():
    assert store.clean("  Идея   новая  ", minimum=3) == "Идея новая"
    with pytest.raises(ValueError):
        store.clean("x", minimum=12)
    with pytest.raises(ValueError):
        store.clean("bad\x00text")
    assert "Первый\nВторой" == store.clean(
        "Первый \n Второй", minimum=4, multiline=True)
    item = SimpleNamespace(
        id=3, author_id=5, topic="Тематика", description="Описание",
        votes_for=4, votes_against=2, status="under_review", status_reason="На проверке",
        thread_id=None,
    )
    embed = suggestion_embed(item)
    assert embed.color.value == 0x4A86F7
    assert embed.fields[2].value == "На рассмотрении"
