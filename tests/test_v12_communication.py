"""v1.2.0: долговечные объявления, восстановление и Help 2.0."""
from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from app.config import GUILD_ID, VERSION
from app.core.database import Database, migrate
from app.core.scheduler import Scheduler
from app.modules.announcements import AnnouncementModal, build_announcement
from app.modules.announcements_manager import AnnouncementManager
from app.modules.help_center import (
    HelpEntry, HelpSearchModal, HelpView, build_pages, detail_embed, find_entries,
    visible_entries,
)
from app.services import announcements as store
from app.ui.announcement_controls import AnnouncementPreviewView, preview_embed
from app.ui.theme import BRAND_BLUE


def url(tmp_path):
    return f"sqlite+aiosqlite:///{tmp_path / 'ann.db'}"


def test_schema_migration_and_components(tmp_path):
    migrate(url(tmp_path))
    migrate(url(tmp_path))
    assert VERSION == "1.2.0"
    with sqlite3.connect(tmp_path / "ann.db") as conn:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0013_announcements"
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"announcements", "announcement_templates", "scheduled_jobs"} <= tables
    cog = SimpleNamespace(bot=SimpleNamespace(config=SimpleNamespace(timezone="UTC", owner_ids=frozenset())))
    modal = AnnouncementModal(cog, channel_id=10, kind="embed", color=BRAND_BLUE)
    assert len(modal.children) == 2
    assert [label.text for label in modal.children] == ["Заголовок", "Текст объявления"]
    assert {"drafts", "scheduled", "history", "send", "edit", "cancel", "duplicate",
            "template-save", "templates", "from-template", "preset"} <= {
        command.name for command in AnnouncementManager.announcements.commands
    }


@pytest.mark.asyncio
async def test_drafts_update_templates_restart_and_preview(tmp_path):
    migrate(url(tmp_path))
    db = Database(url(tmp_path))
    try:
        row = await store.create(db, guild_id=GUILD_ID, author_id=10, channel_id=20,
                                 kind="embed", title="Новости", body="Список изменений",
                                 color=BRAND_BLUE)
        assert row.status == "draft" and row.message_id is None
        embed = preview_embed(row)
        assert embed.color.value == BRAND_BLUE
        assert "ПРЕДПРОСМОТР" in embed.footer.text
        assert len(AnnouncementPreviewView(
            SimpleNamespace(bot=SimpleNamespace(config=SimpleNamespace(owner_ids=frozenset()))),
            row, 10).children) == 5
        row = await store.edit(db, guild_id=GUILD_ID, number=row.id, actor_id=10,
                               title="Новая версия", body="Подробные изменения")
        assert row.title == "Новая версия"
        await store.save_template(db, guild_id=GUILD_ID, slug="release", author_id=10,
                                  number=row.id)
        assert len(await store.templates(db, GUILD_ID)) == 1
        copy = await store.from_template(db, guild_id=GUILD_ID, slug="release",
                                         author_id=11, channel_id=21)
        assert copy.id != row.id and copy.channel_id == 21
        assert copy.body == row.body
        second = await store.duplicate(db, guild_id=GUILD_ID,
                                       number=copy.id, author_id=12)
        assert second.status == "draft" and second.title == "Новая версия"
    finally:
        await db.close()
    db = Database(url(tmp_path))
    try:
        saved = await store.get(db, GUILD_ID, row.id)
        assert saved.title == "Новая версия"
        assert (await store.get(db, 666, row.id)) is None
        assert len(await store.templates(db, GUILD_ID)) == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_atomic_claim_cancel_and_scheduled_restart(tmp_path):
    migrate(url(tmp_path))
    db = Database(url(tmp_path))
    now = 1_800_000_000
    try:
        row = await store.create(
            db, guild_id=GUILD_ID, author_id=10, channel_id=20,
            kind="text", title="Сообщение", body="Новости сообщества",
            color=BRAND_BLUE, now=now,
        )
        await store.schedule(db, guild_id=GUILD_ID, number=row.id,
                             due_at=now + 120, now=now)
        assert await store.claim(db, guild_id=GUILD_ID, number=row.id,
                                 scheduled_only=True, now=now + 60) is None
        await store.cancel(db, guild_id=GUILD_ID, number=row.id, now=now + 90)
        assert await store.claim(db, guild_id=GUILD_ID, number=row.id,
                                 scheduled_only=True, now=now + 200) is None
        row2 = await store.create(
            db, guild_id=GUILD_ID, author_id=10, channel_id=20,
            kind="embed", title="Обновление", body="Новости", color=BRAND_BLUE,
        )
        successes = await asyncio.gather(*(
            store.claim(db, guild_id=GUILD_ID, number=row2.id, scheduled_only=False)
            for _ in range(10)
        ))
        assert sum(x is not None for x in successes) == 1
        assert (await store.get(db, GUILD_ID, row2.id)).attempts == 1
        await store.mark_error(db, guild_id=GUILD_ID, number=row2.id,
                               uncertain=True, detail="Ответ Discord неясен")
        assert (await store.get(db, GUILD_ID, row2.id)).status == "uncertain"
        assert await store.claim(db, guild_id=GUILD_ID, number=row2.id,
                                 scheduled_only=False) is None
        with pytest.raises(ValueError):
            await store.cancel(db, guild_id=GUILD_ID, number=row2.id)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_sent_and_crash_recovery(tmp_path):
    migrate(url(tmp_path))
    db = Database(url(tmp_path))
    try:
        sent = await store.create(
            db, guild_id=GUILD_ID, author_id=10, channel_id=20,
            kind="embed", title="Release", body="Изменения", color=BRAND_BLUE)
        await store.claim(db, guild_id=GUILD_ID, number=sent.id, scheduled_only=False)
        await store.mark_sent(db, guild_id=GUILD_ID, number=sent.id, message_id=999)
        await store.update_sent(db, guild_id=GUILD_ID, number=sent.id,
                                title="Updated", body="Новое описание")
        assert (await store.get(db, GUILD_ID, sent.id)).message_id == 999
        with pytest.raises(ValueError):
            await store.edit(db, guild_id=GUILD_ID, number=sent.id,
                             actor_id=10, title="No", body="No")
        stuck = await store.create(
            db, guild_id=GUILD_ID, author_id=10, channel_id=20,
            kind="text", title="Stuck", body="Во время отправки", color=BRAND_BLUE)
        await store.claim(db, guild_id=GUILD_ID, number=stuck.id, scheduled_only=False)
        assert await store.recover_sending(db, GUILD_ID) == 1
        assert (await store.get(db, GUILD_ID, stuck.id)).status == "uncertain"
        assert await store.recover_sending(db, GUILD_ID) == 0
    finally:
        await db.close()


def test_timezone_dst_and_limits():
    base = int(datetime(2026, 11, 17, tzinfo=timezone.utc).timestamp())
    normal = store.parse_time("2026-11-20 18:30", timezone_name="Europe/Tallinn", now=base)
    assert datetime.fromtimestamp(normal, timezone.utc).hour == 16
    for text in ("2026-03-29 03:30", "2026-10-25 03:30"):
        with pytest.raises(ValueError):
            store.parse_time(text, timezone_name="Europe/Tallinn", now=base - 365 * 86400)
    for date in ("not-a-date", "2025-01-01 12:00", "2029-01-01 12:00"):
        with pytest.raises(ValueError):
            store.parse_time(date, timezone_name="UTC", now=base)
    with pytest.raises(ValueError):
        store.validate("embed", "No", "body", -1)
    with pytest.raises(ValueError):
        store.validate("text", "No", "x" * 1800, BRAND_BLUE)


def test_help_search_access_and_large_category_navigation():
    entries = [
        HelpEntry("/help", "Справка"),
        HelpEntry("/ann", "Отправить объявление"),
        HelpEntry("/botsettings", "Администрирование"),
        HelpEntry("/profile edit", "Редактировать профиль",
                  (("description", "Текст профиля", True),)),
        HelpEntry("/application status", "Проверить заявку"),
    ]
    assert {e.name for e in visible_entries(entries, admin=False, authorized=False)} == {
        "/help", "/application status",
    }
    assert "/ann" not in {e.name for e in visible_entries(entries, admin=False, authorized=True)}
    assert len(visible_entries(entries, admin=True, authorized=False)) == 5
    result = find_entries(entries, "профиль")
    assert result[0].name == "/profile edit"
    assert "<description>" in detail_embed(result[0]).description
    assert not find_entries(entries, "something impossible")
    many = build_pages([HelpEntry(f"/category{i} hello", "Команда")
                        for i in range(34)])
    view = HelpView(many, owner_id=10)
    assert len(view.jump.options) == 25
    assert not view.next_categories.disabled
    view.page = 27
    view.sync()
    assert view.category_offset == 25
    assert len(view.jump.options) == 9
    assert not view.previous_categories.disabled
    assert view.next_categories.disabled
    assert len(HelpSearchModal(view).children) == 1


def test_original_ann_compatibility_and_safe_mentions():
    payload = build_announcement("embed", "News", "@everyone changes")
    assert payload["allowed_mentions"].everyone is False
    assert "@everyone" not in payload["embed"].description
