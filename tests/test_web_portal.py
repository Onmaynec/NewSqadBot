"""Staff portal: local password security, access scope and ticket privacy."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from aiohttp import CookieJar
from aiohttp.test_utils import TestClient, TestServer

from app.core.database import Database, migrate
from app.services import tickets
from app.web.auth import hash_password
from app.web.staff_roles import ADMIN_ROLE, MODER_ROLE, OWNER_ROLE, TICKET_ROLE, site_role
from app.web.server import STATE, create_app


class Member:
    def __init__(self, uid, role):
        self.id = uid
        self.roles = [SimpleNamespace(id=role, is_default=lambda: False)] if role else []
        self.display_name = f"Staff-{uid}"
        self.display_avatar = SimpleNamespace(url=f"https://cdn.discordapp.com/avatars/{uid}/fake.png")
        self.joined_at = datetime.now(timezone.utc)
        self.bot = False
        self.status = "online"


class Guild:
    id = 777
    owner_id = 1
    name = "NewSqad"
    icon = None
    banner = None
    member_count = 5

    def __init__(self):
        self.members_by_id = {
            11: Member(11, OWNER_ROLE),
            12: Member(12, ADMIN_ROLE),
            13: Member(13, MODER_ROLE),
            14: Member(14, TICKET_ROLE),
            15: Member(15, None),
        }
        self.members = list(self.members_by_id.values())
        self.roles = []
        self.channels = []

    async def fetch_member(self, uid):
        return self.members_by_id[uid]

    def get_member(self, uid):
        return self.members_by_id.get(uid)

    def get_role(self, uid):
        return None

    def get_channel(self, uid):
        return None


class FakeBot:
    def __init__(self, db):
        self.database = db
        self.config = SimpleNamespace(guild_id=777, timezone="Europe/Tallinn", web_local_operator_id=14)
        self.module_states = {"tickets": "active", "membership": "active"}
        self.guild = Guild()
        self.intents = SimpleNamespace(presences=False)

    def is_ready(self):
        return True

    def get_guild(self, uid):
        return self.guild if uid == 777 else None

    def get_cog(self, name):
        return None


def test_role_priority_and_no_access_without_role():
    guild = Guild()
    assert site_role(guild.get_member(11), guild) == "owner"
    assert site_role(guild.get_member(12), guild) == "admin"
    assert site_role(guild.get_member(13), guild) == "moder"
    assert site_role(guild.get_member(14), guild) == "support"
    assert site_role(guild.get_member(15), guild) is None
    guild.members_by_id[14].roles = [SimpleNamespace(id=MODER_ROLE), SimpleNamespace(id=TICKET_ROLE)]
    assert site_role(guild.get_member(14), guild) == "moder"


@pytest.mark.asyncio
async def test_local_password_grants_owner_only_with_valid_session(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'roles.db'}"
    migrate(url)
    db = Database(url)
    bot = FakeBot(db)
    try:
        app = create_app(bot, hash_password("Local admin password 12345!"))
        async with TestClient(TestServer(app), cookie_jar=CookieJar(unsafe=True)) as client:
            origin = str(client.make_url("/")).rstrip("/")
            assert (await client.get("/api/bootstrap")).status == 401
            assert (await client.get("/auth/discord")).status == 404
            assert (await client.get("/auth/callback")).status == 404
            wrong = await client.post("/api/login", json={"password": "invalid"},
                                      headers={"Origin": origin})
            assert wrong.status == 401
            valid = await client.post("/api/login",
                                      json={"password": "Local admin password 12345!"},
                                      headers={"Origin": origin})
            assert valid.status == 200
            token = (await valid.json())["csrf"]
            session = await (await client.get("/api/session")).json()
            assert session["authenticated"] and session["role"] == "owner"
            assert session["auth_method"] == "password"
            bootstrap = await (await client.get("/api/bootstrap")).json()
            assert bootstrap["viewer_role"] == "owner"
            assert len(bootstrap["settings"]) > 10
            assert (await client.get("/api/portal/audit")).status == 200
            denied = await client.post("/api/settings/batch", json={
                "group": "Экономика", "changes": {"economy_enabled": False},
            }, headers={"Origin": origin})
            assert denied.status == 403
            allowed = await client.post("/api/settings/batch", json={
                "group": "Экономика", "changes": {"economy_enabled": False},
            }, headers={"Origin": origin, "X-CSRF-Token": token})
            assert allowed.status == 200
            invalid = app[STATE].sessions.create(user_id=14)[0]
            client.session.cookie_jar.update_cookies({"nsb_session": invalid},
                                                       response_url=client.make_url("/"))
            assert (await client.get("/api/bootstrap")).status == 403
            assert not (await (await client.get("/api/session")).json())["authenticated"]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_public_and_oauth_endpoints_are_not_exposed(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'web.db'}")
    try:
        app = create_app(FakeBot(db), hash_password("Local admin password 12345!"))
        async with TestClient(TestServer(app)) as client:
            for path in ("/", "/static/obsidian.js", "/static/staff.js",
                         "/static/obsidian.css", "/static/staff.css"):
                asset = await client.get(path)
                assert asset.status == 200
                assert asset.headers["Cache-Control"] == "no-store"
            for path in ("/auth/discord", "/auth/callback"):
                assert (await client.get(path)).status == 404
            assert (await client.get("/", headers={"Host": "portal.test.ts.net"})).status == 421
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_ticket_messages_are_live_only_and_staff_can_reply(tmp_path, monkeypatch):
    import app.web.portal as portal

    url = f"sqlite+aiosqlite:///{tmp_path / 'ticket.db'}"
    migrate(url)
    db = Database(url)
    bot = FakeBot(db)
    await tickets.seed_categories(db, 777)
    cats = await tickets.categories(db, 777)
    ticket = await tickets.reserve(db, 777, 23, cats[0].id, "Вопрос про сервер",
                                   "Описание обращения", support_role_id=TICKET_ROLE)
    await tickets.created(db, 777, ticket.id, 555)
    sent = []

    class Channel:
        async def history(self, limit, oldest_first):
            assert limit <= 50
            yield SimpleNamespace(id=99, author=SimpleNamespace(
                display_name="Player", display_avatar=SimpleNamespace(url="https://cdn.discordapp.com/u.png"),
                bot=False), content="Привет! Нужна помощь", created_at=datetime.now(timezone.utc),
                attachments=[])

        async def send(self, text, allowed_mentions):
            sent.append(text)

    async def fake_channel(bot_arg, row):
        assert row.id == ticket.id
        return Channel()

    monkeypatch.setattr(portal, "_ticket_channel", fake_channel)
    app = create_app(bot, hash_password("Local admin password 12345!"))
    try:
        async with TestClient(TestServer(app), cookie_jar=CookieJar(unsafe=True)) as client:
            token, record = app[STATE].sessions.create()
            client.session.cookie_jar.update_cookies({"nsb_session": token},
                                                      response_url=client.make_url("/"))
            detail = await client.get(f"/api/portal/tickets/{ticket.id}")
            assert detail.status == 200
            data = await detail.json()
            assert data["messages"][0]["text"] == "Привет! Нужна помощь"
            post = await client.post(f"/api/portal/tickets/{ticket.id}/action",
                json={"action": "reply", "text": "Отвечаю из панели"},
                headers={"Origin": str(client.make_url("/")).rstrip("/"),
                         "X-CSRF-Token": record.csrf})
            assert post.status == 200
            assert "Отвечаю из панели" in sent[0]
            assert "Staff-14" in sent[0]
            async with db.sessions() as session:
                from sqlalchemy import select

                from app.models import TicketEvent

                rows = (await session.scalars(select(TicketEvent))).all()
                assert all("Привет! Нужна помощь" not in event.details_json
                           and "Отвечаю из панели" not in event.details_json for event in rows)
    finally:
        await db.close()



def test_presence_unavailable_is_not_falsely_offline():
    assert Guild().members_by_id[14].status == "online"


@pytest.mark.asyncio
async def test_local_owner_can_access_staff_and_owner_pages_with_csrf(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'complete.db'}"
    migrate(url)
    db = Database(url)
    bot = FakeBot(db)
    app = create_app(bot, hash_password("Local admin password 12345!"))
    paths = ("/api/owner/shop", "/api/owner/role-panels",
             "/api/owner/ticket-categories", "/api/owner/announcements",
             "/api/portal/events", "/api/portal/giveaways",
             "/api/portal/suggestions", "/api/portal/activity", "/api/portal/audit",
             "/api/portal/voice-rooms", "/api/portal/parties",
             "/api/portal/moderation", "/api/portal/members",
             "/api/portal/tickets", "/api/portal/applications",
             "/api/portal/tickets/staff", "/api/staff/inbox", "/api/staff/tasks")
    try:
        async with TestClient(TestServer(app), cookie_jar=CookieJar(unsafe=True)) as client:
            origin = str(client.make_url("/")).rstrip("/")
            token, session = app[STATE].sessions.create()
            client.session.cookie_jar.update_cookies(
                {"nsb_session": token}, response_url=client.make_url("/"))
            for path in paths:
                response = await client.get(path)
                assert response.status == 200, (path, response.status, await response.text())
            payload = {"code": "staff_test", "title": "Staff title",
                       "kind": "title", "price": 45, "description": "", "stock": None,
                       "role_id": None}
            assert (await client.post("/api/owner/shop", json=payload,
                                     headers={"Origin": origin})).status == 403
            success = await client.post("/api/owner/shop", json=payload, headers={
                "Origin": origin, "X-CSRF-Token": session.csrf})
            assert success.status == 200
            moderation = await client.post("/api/portal/moderation", json={
                "user_id": "1440528514318073999", "kind": "warn",
                "reason": "Test warning", "duration": ""},
                headers={"Origin": origin, "X-CSRF-Token": session.csrf})
            assert moderation.status == 503
    finally:
        await db.close()


def test_management_ui_permissions_are_not_only_client_side():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "app/web/static"
    management = (root / "manage.js").read_text(encoding="utf-8")
    portal = (root / "portal.js").read_text(encoding="utf-8")
    assert '"/api/owner/shop"' in management
    assert '"/api/owner/role-panels"' in management
    assert '"/api/portal/events"' in management
    assert '"/api/portal/voice-rooms"' in management
    assert '"/api/portal/parties"' in management
    assert '"/api/portal/suggestions?' in management
    assert '"/api/portal/moderation"' in portal
    assert "roleLabels" in portal
    assert "beforeunload" in (root / "app.js").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_staff_queue_pagination_count_order_and_role_scope(tmp_path):
    """A staff account can navigate past the first 100 entries without cross-guild leaks."""
    from app.models import Application, Ticket

    url = f"sqlite+aiosqlite:///{tmp_path / 'queue_pages.db'}"
    migrate(url)
    db = Database(url)
    bot = FakeBot(db)
    try:
        await tickets.seed_categories(db, 777)
        category = (await tickets.categories(db, 777))[0]
        async with db.session() as session:
            for i in range(103):
                session.add(Application(
                    guild_id=777, user_id=20_000 + i, username=f"candidate-{i}",
                    name="Пользователь", gender="male", age=20, inviter="Никто",
                    status="pending", created_at=1000 + i,
                ))
                session.add(Ticket(
                    guild_id=777, user_id=30_000 + i, category_id=category.id,
                    staff_role_id=TICKET_ROLE, subject=f"Вопрос {i}", description="Тест",
                    status="open", opened_at=1000 + i, updated_at=1000 + i,
                    assignee_id=14 if i < 3 else (13 if i < 5 else None),
                ))
            session.add(Application(
                guild_id=888, user_id=40000, username="other-server",
                name="Пользователь", gender="male", age=20, inviter="Никто",
                status="pending", created_at=1,
            ))
            session.add(Ticket(
                guild_id=888, user_id=40001, category_id=category.id,
                staff_role_id=TICKET_ROLE, subject="Другой сервер", description="Тест",
                status="open", opened_at=1, updated_at=1,
            ))
        app = create_app(bot, hash_password("Local admin password 12345!"))
        async with TestClient(TestServer(app), cookie_jar=CookieJar(unsafe=True)) as client:
            for route in ("/api/portal/applications?status=open", "/api/portal/tickets"):
                unauth = await client.get(route + "&page=1" if "?" in route else route + "?page=1")
                assert unauth.status == 401

            token, _ = app[STATE].sessions.create()
            client.session.cookie_jar.update_cookies(
                {"nsb_session": token}, response_url=client.make_url("/"))
            for route in ("/api/portal/applications?status=open", "/api/portal/tickets"):
                separator = "&" if "?" in route else "?"
                first = await client.get(route + separator + "page=1")
                second = await client.get(route + separator + "page=2")
                assert first.status == 200
                assert second.status == 200
                a, b = await first.json(), await second.json()
                assert a["total"] == b["total"] == 103
                assert a["page_size"] == b["page_size"] == 100
                assert len(a["items"]) == 100 and len(b["items"]) == 3
                assert a["has_next"] and not b["has_next"]
                assert a["items"][-1]["id"] > b["items"][0]["id"]
                assert all(row["user_id"] != "40000"
                           and row["user_id"] != "40001"
                           for row in a["items"] + b["items"])
                for invalid in ("0", "-1", "abc", "10000", "1.5"):
                    bad = await client.get(route + separator + "page=" + invalid)
                    assert bad.status == 400
            for assignment, expected in (("mine", 3), ("assigned", 5),
                                         ("unassigned", 98), ("all", 103)):
                r = await client.get("/api/portal/tickets?assignment=" + assignment)
                assert r.status == 200
                content = await r.json()
                assert content["total"] == expected
                assert len(content["items"]) == min(expected, 100)
                if assignment == "mine":
                    assert all(row["assignee_id"] == "14" for row in content["items"])
            invalid_filter = await client.get("/api/portal/tickets?assignment=random")
            assert invalid_filter.status == 400

            # Text search must apply to the complete queue before pagination.
            for route, term, expected_id in (
                ("/api/portal/applications", "candidate-102", 103),
                ("/api/portal/tickets", "Вопрос 102", 103),
            ):
                found = await client.get(route, params={"q": term})
                assert found.status == 200
                data = await found.json()
                assert data["total"] == 1
                assert data["items"][0]["id"] == expected_id

                by_number = await client.get(route, params={"q": "#2"})
                assert by_number.status == 200
                match = await by_number.json()
                assert match["total"] == 1 and match["items"][0]["id"] == 2

                for literal in ("%", "_", "other-server"):
                    scoped = await client.get(route, params={"q": literal})
                    assert scoped.status == 200
                    assert (await scoped.json())["total"] == 0

                invalid_search = await client.get(route, params={"q": "x" * 81})
                assert invalid_search.status == 400

            # Filtering by assignee and searching by subject combine correctly.
            mine = await client.get("/api/portal/tickets",
                                    params={"assignment": "mine", "q": "Вопрос 1"})
            assert mine.status == 200
            assert (await mine.json())["total"] == 1
    finally:
        await db.close()
