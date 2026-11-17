"""Staff portal: Discord role revocation, OAuth state and privacy of ticket chat."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from aiohttp import CookieJar
from aiohttp.test_utils import TestClient, TestServer

from app.core.database import Database, migrate
from app.services import tickets
from app.web.oauth import ADMIN_ROLE, MODER_ROLE, OWNER_ROLE, TICKET_ROLE, site_role
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
        self.config = SimpleNamespace(guild_id=777, timezone="Europe/Tallinn")
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
async def test_oauth_roles_cannot_access_owner_settings_or_audit(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'roles.db'}"
    migrate(url)
    db = Database(url)
    bot = FakeBot(db)
    try:
        app = create_app(bot, "", discord_client_id="12345678",
                         discord_client_secret="mock_not_used_secret",
                         public_url="https://portal.test.ts.net")
        async with TestClient(TestServer(app), cookie_jar=CookieJar(unsafe=True)) as client:
            cookie_jar = client.session.cookie_jar
            for uid, expected_role in ((11, "owner"), (12, "admin"), (13, "moder"), (14, "support")):
                token, session = app[STATE].sessions.create(user_id=uid)
                cookie_jar.update_cookies({"nsb_session": token}, response_url=client.make_url("/"))
                me = await client.get("/api/session")
                assert (await me.json())["role"] == expected_role
                bootstrap = await (await client.get("/api/bootstrap")).json()
                assert bootstrap["viewer_role"] == expected_role
                if uid == 11:
                    assert len(bootstrap["settings"]) > 10
                else:
                    assert bootstrap["settings"] == []
                    assert bootstrap["roles"] == []

                headers = {"Origin": str(client.make_url("/")).rstrip("/"),
                           "X-CSRF-Token": session.csrf}
                config = await client.post("/api/settings/batch", json={
                    "group": "Экономика", "changes": {"economy_enabled": False},
                }, headers=headers)
                assert config.status == (200 if uid == 11 else 403)
                audit = await client.get("/api/portal/audit")
                assert audit.status == (200 if uid in (11, 12) else 403)
                members = await client.get("/api/portal/members")
                assert members.status == (200 if uid in (11, 12, 13) else 403)
                staff = await client.get("/api/portal/staff")
                assert staff.status == (200 if uid in (11, 12, 13) else 403)
                picker = await client.get("/api/portal/tickets/staff")
                assert picker.status == 200
                tickets_result = await client.get("/api/portal/tickets")
                assert tickets_result.status == 200
                applications_result = await client.get("/api/portal/applications")
                assert applications_result.status == 200

            token, session = app[STATE].sessions.create(user_id=14)
            cookie_jar.update_cookies({"nsb_session": token}, response_url=client.make_url("/"))
            bot.guild.members_by_id[14].roles = []
            me = await client.get("/api/session")
            assert not (await me.json())["authenticated"]
            forbidden = await client.get("/api/portal/tickets")
            assert forbidden.status == 403
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_oauth_start_requires_public_origin_and_state(tmp_path):
    bot = FakeBot(Database(f"sqlite+aiosqlite:///{tmp_path / 'nodb.db'}"))
    try:
        app = create_app(bot, "", discord_client_id="12345678",
                         discord_client_secret="mock_not_used_secret",
                         public_url="https://portal.test.ts.net")
        async with TestClient(TestServer(app)) as client:
            local = await client.get("/auth/discord", allow_redirects=False)
            assert local.status == 302
            assert local.headers["Location"] == "https://portal.test.ts.net/auth/discord"
            public = await client.get("/auth/discord", headers={"Host": "portal.test.ts.net"},
                                      allow_redirects=False)
            assert public.status == 302
            assert public.headers["Location"].startswith("https://discord.com/oauth2/authorize?")
            query = parse_qs(urlsplit(public.headers["Location"]).query)
            assert query["scope"] == ["identify"]
            assert query["redirect_uri"] == ["https://portal.test.ts.net/auth/callback"]
            assert query["state"][0]
            assert "nsb_oauth_state" in public.headers["Set-Cookie"]
            bad = await client.get("/auth/callback?state=bad&code=fake",
                                   headers={"Host": "portal.test.ts.net"})
            assert bad.status == 403
            denied = await client.post("/api/login", json={"password": "any"},
                                       headers={"Origin": str(client.make_url("/")).rstrip("/")})
            assert denied.status == 403
    finally:
        await bot.database.close()


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
    app = create_app(bot, "", discord_client_id="12345678",
                     discord_client_secret="mock_not_used_secret",
                     public_url="https://portal.test.ts.net")
    try:
        async with TestClient(TestServer(app), cookie_jar=CookieJar(unsafe=True)) as client:
            token, record = app[STATE].sessions.create(user_id=14)
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



@pytest.mark.asyncio
async def test_oauth_callback_issues_secure_cookie_and_has_no_access_token(tmp_path, monkeypatch):
    import app.web.server as server

    async def mock_exchange(*args):
        return 12

    monkeypatch.setattr(server, "exchange_code", mock_exchange)
    bot = FakeBot(Database(f"sqlite+aiosqlite:///{tmp_path / 'state.db'}"))
    try:
        app = create_app(bot, "", discord_client_id="12345678",
                         discord_client_secret="mock_not_used_secret",
                         public_url="https://portal.test.ts.net")
        async with TestClient(TestServer(app)) as client:
            response = await client.get("/auth/discord", headers={
                "Host": "portal.test.ts.net"}, allow_redirects=False)
            assert response.status == 302
            generated_state = parse_qs(urlsplit(response.headers["Location"]).query)["state"][0]
            bad = await client.get("/auth/callback?state=wrong&code=fake", headers={
                "Host": "portal.test.ts.net",
                "Cookie": "nsb_oauth_state=" + generated_state}, allow_redirects=False)
            assert bad.status == 403
            redirect = await client.get(
                "/auth/callback?state=" + generated_state + "&code=fake",
                headers={"Host": "portal.test.ts.net",
                         "Cookie": "nsb_oauth_state=" + generated_state},
                allow_redirects=False)
            assert redirect.status == 302
            assert redirect.headers["Location"] == "https://portal.test.ts.net/"
            cookie = redirect.headers["Set-Cookie"]
            assert "nsb_session=" in cookie
            assert "Secure" in cookie and "HttpOnly" in cookie
            assert "mock_not_used_secret" not in cookie
            # OAuth state is single-use and cannot be replayed.
            second = await client.get(
                "/auth/callback?state=" + generated_state + "&code=fake",
                headers={"Host": "portal.test.ts.net",
                         "Cookie": "nsb_oauth_state=" + generated_state},
                allow_redirects=False)
            assert second.status == 403
            site = await client.get("/api/site")
            assert site.status == 200
            assert (await site.json()) == {"name": "NewSqad", "icon_url": None}
    finally:
        await bot.database.close()


def test_presence_unavailable_is_not_falsely_offline():
    assert Guild().members_by_id[14].status == "online"
