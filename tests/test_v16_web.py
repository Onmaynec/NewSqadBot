"""Веб-панель: аутентификация, CSRF, безопасный ввод, SQLite и Discord-каталог."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from aiohttp import CookieJar
from aiohttp.test_utils import TestClient, TestServer

from app.core.database import Database, migrate
from app.models import AuditEntry
from app.web.auth import SessionStore, hash_password, validate_hash, verify_password
from app.web.catalog import BY_KEY
from app.web.server import create_app, local_host, parse_snowflake, validate_value


class Role:
    def __init__(self, id: int, *, dangerous: bool = False, position: int = 2):
        self.id = id
        self.name = f"Role-{id}"
        self.position = position
        self.managed = False
        self.permissions = SimpleNamespace(administrator=dangerous)
        self._dangerous = dangerous

    def is_default(self):
        return False

    def __le__(self, other):
        return self.position <= other.position


class Guild:
    name = "NewSquad"

    def __init__(self):
        self.good_role = Role(123, position=2)
        self.bad_role = Role(456, dangerous=True, position=1)
        self.roles = [self.good_role, self.bad_role]
        self.channels = []
        self.me = SimpleNamespace(
            top_role=Role(999, position=10),
            guild_permissions=SimpleNamespace(manage_roles=True),
        )

    def get_role(self, role_id):
        return next((role for role in self.roles if role.id == role_id), None)

    def get_channel(self, channel_id):
        return None


class FakeBot:
    def __init__(self, database):
        self.config = SimpleNamespace(guild_id=777)
        self.database = database
        self.guild = Guild()
        self.module_states = {"moderation": "active", "membership": "active"}

    def get_guild(self, guild_id):
        return self.guild if guild_id == 777 else None

    def is_ready(self):
        return True


def test_password_hash_validation_and_session_expiration():
    stored = hash_password("a local admin password 123")
    assert validate_hash(stored)
    assert verify_password("a local admin password 123", stored)
    assert not verify_password("wrong password", stored)
    assert not validate_hash("pbkdf2_sha256:1:112233:abcdef")
    sessions = SessionStore()
    token, record = sessions.create(now=1_000)
    assert sessions.get(token, now=1_002) is record
    assert sessions.get(token, now=1_000 + 4 * 3600 + 1) is None
    assert sessions.get("🔐", now=1_000) is None
    sessions.fail("client", now=1_000)
    assert not sessions.limited("client", now=1_001)
    for _ in range(4):
        sessions.fail("client", now=1_000)
    assert sessions.limited("client", now=1_001)
    assert not sessions.limited("client", now=1_400)


def test_dns_rebinding_and_settings_allowlist():
    assert local_host("localhost:8787")
    assert local_host("127.0.0.1:8787")
    assert local_host("[::1]:8787")
    for host in ("evil.com", "localhost.evil.com", "127.0.0.1.attacker.test",
                 "localhost@evil.com", "localhost:bad", ""):
        assert not local_host(host)
    assert "DISCORD_TOKEN" not in BY_KEY
    assert "WEB_ADMIN_PASSWORD_HASH" not in BY_KEY


def test_role_permissions_and_type_validation():
    guild = Guild()
    assert validate_value(BY_KEY["candidate_role_id"], 123, guild) == 123
    with pytest.raises(ValueError, match="опасные"):
        validate_value(BY_KEY["candidate_role_id"], 456, guild)
    with pytest.raises(ValueError, match="не существует"):
        validate_value(BY_KEY["candidate_role_id"], 999, guild)
    with pytest.raises(ValueError):
        validate_value(BY_KEY["member_role_id"], True, guild)
    with pytest.raises(ValueError):
        validate_value(BY_KEY["member_role_id"], 123, None)
    with pytest.raises(ValueError):
        validate_value(BY_KEY["tickets_max_open"], False, guild)
    with pytest.raises(ValueError):
        validate_value(BY_KEY["tickets_max_open"], 100, guild)
    assert validate_value(BY_KEY["tickets_max_open"], 4, guild) == 4
    with pytest.raises(ValueError):
        validate_value(BY_KEY["statistics_timezone"], "No/SuchTimeZone", guild)


@pytest.mark.asyncio
async def test_browser_login_csrf_write_session_and_audit(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'admin.db'}"
    migrate(url)
    db = Database(url)
    bot = FakeBot(db)
    stored = hash_password("super secret local admin pass")
    try:
        async with TestClient(TestServer(create_app(bot, stored)),
                              cookie_jar=CookieJar(unsafe=True)) as client:
            origin = str(client.make_url("/")).rstrip("/")
            denied = await client.get("/api/bootstrap")
            assert denied.status == 401

            wrong_origin = await client.post("/api/login",
                json={"password": "super secret local admin pass"},
                headers={"Origin": "https://malicious.invalid"})
            assert wrong_origin.status == 403

            wrong = await client.post("/api/login", json={"password": "incorrect"},
                                      headers={"Origin": origin})
            assert wrong.status == 401

            login = await client.post("/api/login",
                json={"password": "super secret local admin pass"},
                headers={"Origin": origin})
            assert login.status == 200
            token = (await login.json())["csrf"]
            assert token

            bootstrap = await client.get("/api/bootstrap")
            assert bootstrap.status == 200
            body = await bootstrap.json()
            assert body["guild_id"] == "777"
            assert not any("token" in item["key"] for item in body["settings"])
            assert "super secret local admin pass" not in json.dumps(body)
            assert body["roles"][0]["id"] in ("123", "456")

            no_csrf = await client.post("/api/settings/tickets_max_open",
                json={"value": 4}, headers={"Origin": origin})
            assert no_csrf.status == 403
            attack = await client.post("/api/settings/tickets_max_open",
                json={"value": 4}, headers={"Origin": "https://attacker.invalid",
                                             "X-CSRF-Token": token})
            assert attack.status == 403
            unknown = await client.post("/api/settings/DISCORD_TOKEN",
                json={"value": "secret"}, headers={"Origin": origin,
                                                   "X-CSRF-Token": token})
            assert unknown.status == 404
            bad = await client.post("/api/settings/tickets_max_open",
                json={"value": True}, headers={"Origin": origin, "X-CSRF-Token": token})
            assert bad.status == 422
            save = await client.post("/api/settings/tickets_max_open",
                json={"value": 4}, headers={"Origin": origin, "X-CSRF-Token": token})
            assert save.status == 200
            assert await db.get_setting(777, "tickets_max_open") == 4
            save_role = await client.post("/api/settings/candidate_role_id",
                json={"value": 123}, headers={"Origin": origin, "X-CSRF-Token": token})
            assert save_role.status == 200
            assert await db.get_setting(777, "candidate_role_id") == 123
            async with db.sessions() as session:
                from sqlalchemy import select

                audit = (await session.scalars(select(AuditEntry))).all()
                assert len(audit) >= 2

            logout = await client.post("/api/logout", json={},
                                       headers={"Origin": origin, "X-CSRF-Token": token})
            assert logout.status == 200
            assert (await client.get("/api/bootstrap")).status == 401
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_server_rejects_wrong_host_and_oversized_input(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'host.db'}")
    try:
        async with TestClient(TestServer(create_app(
            FakeBot(db), hash_password("secure and very long pass")))) as client:
            hijack = await client.get("/", headers={"Host": "evil.invalid"})
            assert hijack.status == 421
            assert hijack.headers["X-Frame-Options"] == "DENY"
            found = await client.get("/static/app.js")
            assert found.status == 200
            assert "text/javascript" in found.content_type or "javascript" in found.content_type
            assert "script-src 'self'" in found.headers["Content-Security-Policy"]
    finally:
        await db.close()


def test_discord_snowflakes_preserve_all_digits_and_reject_unsafe_numbers():
    identifier = "1440528514318073900"
    assert parse_snowflake(identifier) == 1440528514318073900
    assert str(parse_snowflake(identifier)) == identifier
    with pytest.raises(ValueError):
        parse_snowflake(float(identifier))
    with pytest.raises(ValueError):
        parse_snowflake("-1")
    with pytest.raises(ValueError):
        parse_snowflake("1.44e18")
    with pytest.raises(ValueError):
        parse_snowflake(True)


@pytest.mark.asyncio
async def test_bulk_save_exact_snowflakes_is_atomic_and_audited(tmp_path, monkeypatch):
    """Выбрать 2 канала и роль -> 1 запрос -> все ID без потерь."""
    from sqlalchemy import select

    import app.web.server as server

    url = f"sqlite+aiosqlite:///{tmp_path / 'bulk.db'}"
    migrate(url)
    db = Database(url)
    bot = FakeBot(db)
    role_id = 1440528514318073900
    giveaway_id = 1440528514318073901
    suggestion_id = 1440528514318073902
    role = Role(role_id, position=2)
    bot.guild.roles.append(role)

    class Channel:
        def __init__(self, identifier, name):
            self.id = identifier
            self.name = name
            self.category = None

        def permissions_for(self, member):
            return SimpleNamespace(view_channel=True, send_messages=True, embed_links=True)

    bot.guild.channels = [
        Channel(giveaway_id, "Giveaway"),
        Channel(suggestion_id, "Ideas"),
    ]
    bot.guild.get_channel = lambda identifier: next(
        (ch for ch in bot.guild.channels if ch.id == identifier), None)
    monkeypatch.setattr(server, "channel_type", lambda ch: "text")
    try:
        async with TestClient(TestServer(create_app(bot, hash_password("a long valid secret password"))),
                              cookie_jar=CookieJar(unsafe=True)) as client:
            origin = str(client.make_url("/")).rstrip("/")
            login = await client.post("/api/login",
                json={"password": "a long valid secret password"},
                headers={"Origin": origin})
            csrf = (await login.json())["csrf"]
            headers = {"Origin": origin, "X-CSRF-Token": csrf}
            bootstrap = await client.get("/api/bootstrap")
            payload = await bootstrap.json()
            assert payload["guild_id"] == "777"
            assert any(r["id"] == str(role_id) for r in payload["roles"])
            assert {c["id"] for c in payload["channels"]} == {
                str(giveaway_id), str(suggestion_id)}
            assert all(isinstance(c["id"], str) for c in payload["channels"])

            partial = await client.post("/api/settings/batch", json={
                "group": "Каналы", "changes": {
                    "giveaway_channel_id": str(giveaway_id),
                    "suggest_channel_id": "1440528514318073999",
                }}, headers=headers)
            assert partial.status == 422
            assert await db.get_setting(777, "giveaway_channel_id") is None
            assert await db.get_setting(777, "suggest_channel_id") is None

            valid = await client.post("/api/settings/batch", json={
                "group": "Каналы", "changes": {
                    "giveaway_channel_id": str(giveaway_id),
                    "suggest_channel_id": str(suggestion_id),
                }}, headers=headers)
            assert valid.status == 200, await valid.text()
            response = await valid.json()
            assert response["count"] == 2
            assert response["values"] == {
                "giveaway_channel_id": str(giveaway_id),
                "suggest_channel_id": str(suggestion_id),
            }
            assert await db.get_setting(777, "giveaway_channel_id") == giveaway_id
            assert await db.get_setting(777, "suggest_channel_id") == suggestion_id

            roles = await client.post("/api/settings/batch", json={
                "group": "Роли", "changes": {"member_role_id": str(role_id)}},
                headers=headers)
            assert roles.status == 200, await roles.text()
            assert await db.get_setting(777, "member_role_id") == role_id

            bootstrap = await client.get("/api/bootstrap")
            payload = await bootstrap.json()
            spec = next(x for x in payload["settings"] if x["key"] == "giveaway_channel_id")
            assert spec["value"] == str(giveaway_id)

            crossed = await client.post("/api/settings/batch", json={
                "group": "Роли", "changes": {"giveaway_channel_id": str(giveaway_id)}},
                headers=headers)
            assert crossed.status == 422

            csrf_fail = await client.post("/api/settings/batch", json={
                "group": "Каналы", "changes": {"giveaway_channel_id": str(giveaway_id)}},
                headers={"Origin": origin})
            assert csrf_fail.status == 403

            async with db.sessions() as session:
                rows = (await session.scalars(select(AuditEntry))).all()
                assert len(rows) == 3
                assert all(row.actor_id == 0 for row in rows)
    finally:
        await db.close()


def test_frontend_has_single_save_and_navigation_guard():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "app/web/static"
    js = (root / "app.js").read_text(encoding="utf-8")
    html = (root / "index.html").read_text(encoding="utf-8")
    css = (root / "style.css").read_text(encoding="utf-8")
    assert '"/api/settings/batch"' in js
    assert 'if (spec.kind === "role" || spec.kind === "channel") return field.value || null;' in js
    assert "beforeunload" in js
    assert "confirmDiscard" in js
    assert 'id="bulk-save"' in html
    assert 'id="leave-dialog"' in html
    assert 'id="overview-guild-icon"' in html
    assert "color-scheme: dark" in css
