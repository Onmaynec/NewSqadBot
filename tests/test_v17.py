"""v1.7.0: feedback privacy, live dashboard, notifications and four-role HTTP guard."""
from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from aiohttp import CookieJar
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import func, select, update

from app.core.database import Database, migrate
from app.models import Application, SupportSurvey, TemporaryRole, Ticket, WebNotice
from app.services import support_quality, tickets, web_notifications
from app.web.staff_roles import ADMIN_ROLE, MODER_ROLE, OWNER_ROLE, TICKET_ROLE
from app.web.server import STATE, create_app


class Member:
    def __init__(self, uid: int, role: int):
        self.id = uid
        self.roles = [SimpleNamespace(id=role, is_default=lambda: False)]
        self.display_name = f"Staff-{uid}"
        self.display_avatar = SimpleNamespace(url="https://cdn.discordapp.com/123.png")
        self.joined_at = datetime.now(timezone.utc)
        self.status = "online"
        self.bot = False


class Guild:
    id = 777
    owner_id = 1
    name = "NewSqad"
    icon = None
    banner = None
    member_count = 4

    def __init__(self):
        self.members = [Member(11, OWNER_ROLE), Member(12, ADMIN_ROLE),
                        Member(13, MODER_ROLE), Member(14, TICKET_ROLE)]

    async def fetch_member(self, user_id):
        return next(member for member in self.members if member.id == user_id)

    def get_member(self, user_id):
        return next((person for person in self.members if person.id == user_id), None)

    def get_role(self, role_id):
        return None


class FakeBot:
    def __init__(self, db):
        self.database = db
        self.config = SimpleNamespace(guild_id=777, timezone="Europe/Tallinn")
        self.module_states = {"moderation": "active", "tickets": "active"}
        self.guild = Guild()
        self.intents = SimpleNamespace(presences=False)
        self.scheduler = SimpleNamespace(running=True)
        self.events = SimpleNamespace(running=True)

    def get_guild(self, guild_id):
        return self.guild if guild_id == 777 else None

    def is_ready(self):
        return True


def database_url(tmp_path):
    return f"sqlite+aiosqlite:///{tmp_path / 'v17.db'}"


def test_schema_migrates_to_v17_without_ticket_text_storage(tmp_path):
    url = database_url(tmp_path)
    migrate(url)
    migrate(url)
    with sqlite3.connect(tmp_path / "v17.db") as sql:
        assert sql.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0016_staff_command_center"
        columns = {row[1] for row in sql.execute("PRAGMA table_info(support_surveys)")}
        assert {"requester_id", "staff_id", "response_seconds", "rating"} <= columns
        assert "subject" not in columns and "message" not in columns and "ticket_id" not in columns
        assert {"support_surveys", "web_notices", "web_notice_reads"} <= {
            row[0] for row in sql.execute("SELECT name FROM sqlite_master WHERE type='table'")}


@pytest.mark.asyncio
async def test_support_survey_created_only_on_deleted_ticket_and_anonymizes_rating(tmp_path):
    url = database_url(tmp_path)
    migrate(url)
    db = Database(url)
    try:
        await tickets.seed_categories(db, 777)
        category = (await tickets.categories(db, 777))[0]
        record = await tickets.reserve(db, 777, 111, category.id,
                                       "Секретная тема", "Секретный текст чата",
                                       support_role_id=TICKET_ROLE)
        assert await tickets.created(db, 777, record.id, 9898)
        assert await tickets.claim(db, 777, record.id, 14)
        claimed = await tickets.get(db, 777, record.id)
        assert claimed.first_response_at is None  # Принятие не считается ответом.
        async with db.session() as session:
            await session.execute(update(Ticket).where(Ticket.id == record.id)
                                  .values(opened_at=int(time.time()) - 600))
        await tickets.touch(db, 777, record.id, 14, staff_reply=True)
        assert await tickets.start_close(db, 777, record.id, 14, "Решено")
        assert await tickets.erase_ticket(db, 777, record.id)
        assert await tickets.get(db, 777, record.id) is None
        surveys = await support_quality.pending(db, 777, requester_id=111)
        assert len(surveys) == 1
        feedback = surveys[0]
        assert feedback.staff_id == 14 and feedback.response_seconds >= 599
        assert await support_quality.rate(db, 777, feedback.id, 111, 5)
        assert not await support_quality.rate(db, 777, feedback.id, 111, 1)
        async with db.sessions() as session:
            saved = await session.get(SupportSurvey, feedback.id)
            assert saved.requester_id == 0 and saved.rating == 5
            assert await session.scalar(select(func.count(Ticket.id))) == 0
        summary = await support_quality.summary(db, 777)
        assert summary["ratings"] == 1 and summary["average_rating"] == 5
        assert summary["closed_tickets"] == 1
        assert summary["avg_first_response_seconds"] >= 599
        assert summary["staff"][0]["user_id"] == "14"
        with pytest.raises(ValueError):
            await support_quality.rate(db, 777, feedback.id, 111, 6)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_notice_deduplication_and_user_scope(tmp_path):
    url = database_url(tmp_path)
    migrate(url)
    db = Database(url)
    now = int(time.time())
    try:
        async with db.session() as session:
            session.add(Application(
                guild_id=777, user_id=200, username="Candidate",
                name="Test", age=20, gender="male", inviter="Friend",
                created_at=now, status="pending"))
            session.add(TemporaryRole(
                guild_id=777, user_id=200, role_id=555, granted_by=11,
                granted_at=now, expires_at=now + 200, status="failed"))
        assert await web_notifications.sync_sources(db, 777, now=now) >= 2
        await web_notifications.sync_sources(db, 777, now=now)
        async with db.sessions() as session:
            assert await session.scalar(select(func.count(WebNotice.id))) == 2
        owner = await web_notifications.inbox(db, 777, 11, "owner")
        support = await web_notifications.inbox(db, 777, 14, "support")
        assert owner["unread"] == 2
        assert support["unread"] == 1
        role_error = next(n for n in owner["items"] if n["kind"] == "role_failure")
        assert not any(n["kind"] == "role_failure" for n in support["items"])
        assert await web_notifications.mark_read(db, 777, 14, "support",
                                                 notice_id=role_error["id"]) == 0
        assert await web_notifications.mark_read(db, 777, 11, "owner",
                                                 notice_id=role_error["id"]) == 1
        assert (await web_notifications.inbox(db, 777, 11, "owner"))["unread"] == 1
        assert (await web_notifications.inbox(db, 777, 12, "admin"))["unread"] == 2
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_dashboard_quality_and_notice_http_permissions(tmp_path):
    url = database_url(tmp_path)
    migrate(url)
    db = Database(url)
    bot = FakeBot(db)
    app = create_app(bot, "", discord_client_id="1234567890",
                     discord_client_secret="fake_secret_for_test",
                     public_url="https://portal.example.ts.net")
    try:
        async with TestClient(TestServer(app), cookie_jar=CookieJar(unsafe=True)) as client:
            for uid, role in ((11, "owner"), (12, "admin"), (13, "moder"), (14, "support")):
                token, record = app[STATE].sessions.create(user_id=uid)
                client.session.cookie_jar.update_cookies(
                    {"nsb_session": token}, response_url=client.make_url("/"))
                dashboard = await client.get("/api/portal/dashboard")
                assert dashboard.status == 200, await dashboard.text()
                data = await dashboard.json()
                assert data["role"] == role and data["discord_connected"]
                assert not data["presence_available"] and data["online_staff"] is None
                quality = await client.get("/api/portal/support-quality?days=30")
                assert quality.status == (200 if uid in (11, 12) else 403)
                inbox = await client.get("/api/portal/notifications")
                assert inbox.status == 200
                headers = {"Origin": str(client.make_url("/")).rstrip("/"),
                           "X-CSRF-Token": record.csrf}
                no_csrf = await client.post("/api/portal/notifications/all/read",
                                            json={}, headers={"Origin": headers["Origin"]})
                assert no_csrf.status == 403
                assert (await client.post("/api/portal/notifications/all/read",
                                          json={}, headers=headers)).status == 200
    finally:
        await db.close()


def test_frontend_dashboard_and_notification_views_present():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "app/web/static"
    js = (root / "portal.js").read_text(encoding="utf-8")
    assert '"/api/portal/dashboard"' in js
    assert '"/api/portal/support-quality?days="' in js
    assert '"/api/portal/notifications"' in js
    assert "setInterval" in js and "20000" in js and "30000" in js
    html = (root / "index.html").read_text(encoding="utf-8")
    assert 'id="notification-count"' in html and 'id="overview-live"' in html


@pytest.mark.asyncio
async def test_notifications_follow_assignment_and_one_hour_event_and_erase_on_close(tmp_path):
    from app.services import events

    url = database_url(tmp_path)
    migrate(url)
    db = Database(url)
    now = int(time.time())
    try:
        await tickets.seed_categories(db, 777)
        category = (await tickets.categories(db, 777))[0]
        record = await tickets.reserve(db, 777, 200, category.id, "Вопрос",
                                       "Не хранить содержимое после закрытия",
                                       support_role_id=TICKET_ROLE)
        assert await tickets.created(db, 777, record.id, 11111)
        assert await tickets.claim(db, 777, record.id, 14)
        await events.create_event(
            db, guild_id=777, creator_id=11, host_id=11,
            channel_id=1234, title="Общая игра", description="Игровая встреча",
            starts_at=now + 1800, timezone_name="Europe/Tallinn")
        await web_notifications.sync_sources(db, 777, now=now)
        assignee = await web_notifications.inbox(db, 777, 14, "support")
        admin = await web_notifications.inbox(db, 777, 12, "admin")
        assert any(item["kind"] == "ticket_assigned" for item in assignee["items"])
        assert not any(item["kind"] == "ticket_assigned" for item in admin["items"])
        assert any(item["kind"] == "event_upcoming" for item in admin["items"])
        assert not any(item["kind"] == "event_upcoming" for item in assignee["items"])
        assert await tickets.start_close(db, 777, record.id, 14, "Закрыто")
        assert await tickets.erase_ticket(db, 777, record.id)
        after = await web_notifications.inbox(db, 777, 14, "support")
        assert not any(item["kind"] == "ticket_assigned" for item in after["items"])
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_persistent_survey_view_and_expired_invites(tmp_path):
    from app.modules.tickets import SupportSurveyView

    view = SupportSurveyView(SimpleNamespace(), survey_id=999, owner_id=222)
    assert view.is_persistent()
    assert {button.custom_id for button in view.children} == {
        f"newsqad:support:rate:999:{score}" for score in range(1, 6)}
    url = database_url(tmp_path)
    migrate(url)
    db = Database(url)
    now = int(time.time())
    try:
        async with db.session() as session:
            session.add(SupportSurvey(
                guild_id=777, requester_id=222, staff_id=14, created_at=now - 8 * 86400,
                expires_at=now - 1, response_seconds=20))
        survey = (await support_quality.pending(db, 777, now=now))
        assert not survey
        async with db.sessions() as session:
            row = await session.scalar(select(SupportSurvey))
        assert not await support_quality.rate(db, 777, row.id, 222, 5, now=now)
        await support_quality.purge(db, 777, now=now)
        async with db.sessions() as session:
            assert await session.scalar(select(func.count(SupportSurvey.id))) == 0
    finally:
        await db.close()
