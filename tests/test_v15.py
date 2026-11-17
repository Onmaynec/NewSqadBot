"""Интеграционные проверки NewSqadBot v1.5.0 на настоящей SQLite."""
from __future__ import annotations

import asyncio
import sqlite3
import time

import pytest

from app.config import VERSION
from app.core.database import Database, migrate
from app.models import (
    Application, CommunityEvent, EventSignup, ModerationAction, ModerationCase,
    RolePanel, RolePanelOption,
)
from app.services import applications, moderation_cases, suggestions
from app.services import achievements, quests


def url(tmp_path):
    return f"sqlite+aiosqlite:///{tmp_path / 'v15.db'}"


def test_schema_category_upgrade_preserves_data(tmp_path):
    dbfile = tmp_path / "v15.db"
    migrate(url(tmp_path))
    migrate(url(tmp_path))
    assert VERSION == "1.5.0"
    with sqlite3.connect(dbfile) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0014_community_categories"
        roles = [r[1] for r in db.execute("PRAGMA table_info(role_panel_options)")]
        ideas = [r[1] for r in db.execute("PRAGMA table_info(community_suggestions)")]
        assert "category" in roles
        assert "category" in ideas


@pytest.mark.asyncio
async def test_case_mutation_guild_scope_timeline_and_cas(tmp_path):
    migrate(url(tmp_path))
    db = Database(url(tmp_path))
    try:
        now = int(time.time())
        async with db.session() as session:
            session.add(ModerationCase(guild_id=100, user_id=200, moderator_id=300,
                kind="warn", status="active", reason="old", created_at=now, dm_status="done"))
        items = await moderation_cases.find(db, 100, user_id=200)
        assert len(items) == 1
        assert await moderation_cases.find(db, 101, number=items[0].id) == []
        done = await moderation_cases.apply(db, 100, items[0].id, actor_id=400,
            action="reason_changed", details="reason updated", changes={"reason": "new"})
        assert done
        changed = await moderation_cases.find(db, 100, number=items[0].id)
        assert changed[0].reason == "new"
        journal = await moderation_cases.actions(db, 100, items[0].id)
        assert len(journal) == 1 and journal[0].actor_id == 400
        assert await moderation_cases.actions(db, 101, items[0].id) == []
        result = await moderation_cases.apply(db, 100, items[0].id, actor_id=400,
            action="revoked", details="reversed decision",
            changes={"status": "revoked", "closed_by": 400, "completed_at": now})
        assert result
        assert not await moderation_cases.apply(db, 100, items[0].id, actor_id=400,
            action="reason_changed", details="another edit", changes={"reason": "bad"})
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_concurrent_case_revokes_only_one_wins(tmp_path):
    migrate(url(tmp_path))
    db = Database(url(tmp_path))
    try:
        async with db.session() as session:
            session.add(ModerationCase(guild_id=4, user_id=5, moderator_id=6,
                kind="warn", status="active", reason="note", created_at=int(time.time()),
                dm_status="done"))
        case = (await moderation_cases.find(db, 4))[0]
        async def revoke():
            return await moderation_cases.apply(db, 4, case.id, actor_id=9,
                action="revoked", details="Important reason",
                changes={"status": "revoked", "closed_by": 9, "completed_at": int(time.time())})
        values = await asyncio.gather(revoke(), revoke())
        assert values.count(True) == 1
        assert len(await moderation_cases.actions(db, 4, case.id)) == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_membership_handoff_question_and_answer_history(tmp_path):
    migrate(url(tmp_path))
    db = Database(url(tmp_path))
    try:
        app = await applications.submit(db, guild_id=10, user_id=11,
            username="candidate", name="Alex", gender="male", age=20, inviter="friend")
        assert isinstance(app, Application)
        assert await applications.assign_reviewer(db, 10, app.id, actor_id=99, reviewer_id=55)
        assert not await applications.assign_reviewer(db, 999, app.id, actor_id=99, reviewer_id=55)
        assert await applications.transition(db, app.id, expected=("in_review",),
            target="needs_info", actor_id=55, reason="Уточните приглашение")
        assert await applications.submit_answer(db, 10, app.id, 11, "Меня пригласил Иван")
        assert not await applications.submit_answer(db, 10, app.id, 11, "Повтор")
        item = await applications.get_application(db, app.id)
        assert item.reviewer_id == 55
        events = await applications.timeline(db, 10, app.id)
        assert len(events) >= 4
        assert "Ответ кандидата" in events[0].reason
        assert await applications.timeline(db, 20, app.id) == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_role_options_and_idea_categories(tmp_path):
    migrate(url(tmp_path))
    db = Database(url(tmp_path))
    try:
        async with db.session() as session:
            panel = RolePanel(guild_id=1, channel_id=2, title="Games", description="",
                mode="multi", enabled=True, created_by=3, created_at=int(time.time()))
            session.add(panel)
            await session.flush()
            session.add(RolePanelOption(panel_id=panel.id, role_id=99,
                label="Minecraft", category="games"))
        idea = await suggestions.create(db, guild_id=1, author_id=10, channel_id=20,
            topic="Новый игровой канал", description="Предлагаю игровой канал для друзей",
            category="games", cooldown=0)
        matches = await suggestions.list_items(db, 1, category="games", author_id=10)
        assert matches[0].id == idea.id
        assert await suggestions.list_items(db, 1, category="server") == []
        with pytest.raises(ValueError, match="категория"):
            await suggestions.create(db, guild_id=1, author_id=10, channel_id=20,
                topic="Идея", description="Описание новой идеи для сервера",
                category="invalid", cooldown=0)
        async with db.sessions() as session:
            loaded = await session.get(RolePanelOption, 1)
            assert loaded.category == "games"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_event_attendance_is_counted_in_quests_and_achievements(tmp_path):
    migrate(url(tmp_path))
    db = Database(url(tmp_path))
    try:
        now = int(time.time())
        async with db.session() as session:
            event = CommunityEvent(
                guild_id=1, creator_id=2, host_id=2, channel_id=4,
                title="Игра вместе", description="Официальный ивент",
                timezone="UTC", starts_at=now-1000, ends_at=now-100,
                registration_ends_at=now-1100, capacity=10,
                status="finished", created_at=now-2000, updated_at=now)
            session.add(event)
            await session.flush()
            session.add(EventSignup(event_id=event.id, user_id=15, status="confirmed",
                registered_at=now-1800, attended_at=now-110, confirmed_by=2))
        weekly = await quests.list_quests(db, 1, 15, "UTC", "weekly", now=now)
        claim = next(x for x in weekly if x.quest.code == "weekly_event")
        assert claim.value == 1 and not claim.claimed
        ach = await achievements.list_achievements(db, 1, 15)
        assert any(x.achievement.code == "first_event" and x.value == 1 for x in ach)
    finally:
        await db.close()
