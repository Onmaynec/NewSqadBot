"""v2.0.0 Staff Command Center: local auth, workflows, dedupe and privacy."""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from aiohttp import CookieJar
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import func, select

from app.config import GUILD_ID
from app.core.database import Database, migrate
from app.models import Application, AuditEntry, StaffOperation
from app.services import tickets
from app.web.auth import hash_password
from app.web.server import create_app

PASSWORD = "Local staff owner secret 2026"


class LocalGuild:
    id = GUILD_ID
    owner_id = 61
    name = "NewSqad"
    icon = None
    banner = None
    member_count = 17
    roles = ()
    channels = ()
    members = ()
    me = None

    def get_member(self, uid):
        if uid == self.owner_id:
            return SimpleNamespace(id=uid, display_name="Локальный администратор",
                                   display_avatar=SimpleNamespace(url="https://cdn.discordapp.com/embed/avatars/0.png"))
        return None

    async def fetch_member(self, uid):
        return self.get_member(uid)

    def get_role(self, _uid):
        return None

    def get_channel(self, _id):
        return None


class LocalBot:
    def __init__(self, database):
        self.database = database
        self.config = SimpleNamespace(guild_id=GUILD_ID, web_local_operator_id=61)
        self.module_states = {"membership": "active", "tickets": "active"}
        self.guild = LocalGuild()
        self.intents = SimpleNamespace(presences=False)
        self.scheduler = None
        self.events = None
        self.operations = None
        self.backups = None

    def get_guild(self, _id):
        return self.guild if _id == GUILD_ID else None

    def is_ready(self):
        return True

    def get_cog(self, _name):
        return None


async def login(client):
    origin = str(client.make_url("/")).rstrip("/")
    response = await client.post("/api/login", json={"password": PASSWORD},
                                 headers={"Origin": origin})
    assert response.status == 200
    return origin, (await response.json())["csrf"]


async def seed(db):
    await tickets.seed_categories(db, GUILD_ID)
    category = (await tickets.categories(db, GUILD_ID))[0]
    async with db.session() as session:
        session.add(Application(
            guild_id=GUILD_ID, user_id=711, username="user711",
            name="Игрок", gender="male", age=21, inviter="Друг",
            status="pending", created_at=int(time.time()),
        ))
    row = await tickets.reserve(db, GUILD_ID, 722, category.id, "Поддержка",
                                "Проблема в игре", support_role_id=61)
    await tickets.created(db, GUILD_ID, row.id, 888)


@pytest.mark.asyncio
async def test_local_password_queues_tasks_templates_and_analytics(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'staff.db'}"
    migrate(database_url)
    db = Database(database_url)
    await seed(db)
    bot = LocalBot(db)
    app = create_app(bot, hash_password(PASSWORD))
    try:
        async with TestClient(TestServer(app), cookie_jar=CookieJar(unsafe=True)) as client:
            assert (await client.get("/api/staff/inbox")).status == 401
            origin, csrf = await login(client)
            headers = {"Origin": origin, "X-CSRF-Token": csrf}
            inbox = await client.get("/api/staff/inbox")
            assert inbox.status == 200
            rows = (await inbox.json())["items"]
            assert {row["kind"] for row in rows} == {"application", "ticket"}
            assert all(row["user_id"] in ("711", "722") for row in rows)
            assert (await client.get("/api/staff/tasks")).status == 200
            unauthorized = await client.post("/api/staff/tasks", json={"title": "Задача"},
                                             headers={"Origin": origin})
            assert unauthorized.status == 403
            created = await client.post("/api/staff/tasks", json={
                "title": "Проверить обращение", "description": "По очереди", "assigned_to": "61",
            }, headers=headers)
            assert created.status == 200, await created.text()
            tid = (await created.json())["id"]
            mine = (await (await client.get("/api/staff/tasks?mine=1")).json())["items"]
            assert mine[0]["title"] == "Проверить обращение"
            done = await client.post(f"/api/staff/tasks/{tid}/status",
                                     json={"status": "done"}, headers=headers)
            assert done.status == 200
            template = await client.post("/api/staff/templates", json={
                "scope": "ticket", "title": "Приветствие",
                "body": "Здравствуйте, мы проверяем обращение.",
            }, headers=headers)
            assert template.status == 200, await template.text()
            rows = (await (await client.get("/api/staff/templates")).json())["items"]
            assert len(rows) == 1
            assert rows[0]["body"].startswith("Здравствуйте")
            stats = await client.get("/api/staff/analytics?days=30")
            assert stats.status == 200
            assert (await stats.json())["totals"]["applications"] == 1
            health = await client.get("/api/staff/health")
            assert health.status == 200
            timeline = await client.get("/api/staff/timeline?user_id=61")
            assert timeline.status == 200
            entries = (await timeline.json())["items"]
            assert any(item["action"].startswith("staff.task") for item in entries)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_bulk_application_claim_is_sequential_and_idempotent(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'bulk.db'}"
    migrate(database_url)
    db = Database(database_url)
    await seed(db)
    app = create_app(LocalBot(db), hash_password(PASSWORD))
    try:
        async with TestClient(TestServer(app), cookie_jar=CookieJar(unsafe=True)) as client:
            origin, csrf = await login(client)
            headers = {"Origin": origin, "X-CSRF-Token": csrf}
            request = {"request_key": "staff-test-key-00001", "ids": [1],
                       "action": "claim", "confirm": "CONFIRM", "reason": ""}
            first = await client.post("/api/staff/bulk", json=request, headers=headers)
            assert first.status == 200, await first.text()
            assert (await first.json())["success"] == 1
            again = await client.post("/api/staff/bulk", json=request, headers=headers)
            assert again.status == 200
            assert (await again.json())["items"][0]["status"] == "duplicate"
            wrong = await client.post("/api/staff/bulk",
                json={**request, "request_key": "staff-test-key-00002", "action": "reject"},
                headers=headers)
            assert wrong.status == 422
            async with db.sessions() as session:
                claimed = await session.get(Application, 1)
                assert claimed.reviewer_id == 61
                assert await session.scalar(select(func.count()).select_from(StaffOperation)) == 1
                audit = (await session.scalars(select(AuditEntry))).all()
                assert any(x.action == "staff.operation" for x in audit)
                assert all("Описание" not in x.data_json for x in audit)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_forged_oauth_cookie_is_denied_and_templates_not_sent_automatically(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'security.db'}"
    migrate(database_url)
    db = Database(database_url)
    app = create_app(LocalBot(db), hash_password(PASSWORD))
    try:
        async with TestClient(TestServer(app), cookie_jar=CookieJar(unsafe=True)) as client:
            token, _ = app[__import__("app.web.server", fromlist=["STATE"]).STATE].sessions.create(user_id=999)
            client.session.cookie_jar.update_cookies({"nsb_session": token},
                                                     response_url=client.make_url("/"))
            assert (await client.get("/api/staff/analytics")).status == 403
            assert (await client.get("/auth/discord")).status == 404
    finally:
        await db.close()
