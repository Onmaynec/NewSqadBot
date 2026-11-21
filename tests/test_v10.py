"""v0.10.0: Party Finder и атомарные операции Membership."""
from __future__ import annotations

import asyncio
import sqlite3
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.config import GUILD_ID, VERSION
from app.core.catalog import implemented
from app.core.database import Database, migrate
from app.services import applications, party_finder
from app.services.membership_access import approved_member


def test_v010_schema(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'schema.db'}"
    migrate(url)
    migrate(url)
    assert VERSION == "2.0.0"
    assert implemented("party_finder")
    with sqlite3.connect(tmp_path / "schema.db") as db:
        db.execute("PRAGMA foreign_keys=ON")
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0016_staff_command_center"
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"party_listings", "party_members"} <= tables
        columns = {r[1] for r in db.execute("PRAGMA table_info(membership_applications)")}
        assert "candidate_role_status" in columns


@pytest.mark.asyncio
async def test_party_lifecycle_and_limits(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'party.db'}"
    migrate(url)
    db = Database(url)
    now = int(time.time())
    try:
        p = await party_finder.create(db, guild_id=GUILD_ID, owner_id=11, game="Minecraft",
            description="Строим базу", capacity=3, expires_at=now + 7200, channel_id=55)
        assert p.status == "open" and p.member_count == 1
        assert await party_finder.members(db, p.id) == [11]
        with pytest.raises(ValueError, match="активное"):
            await party_finder.create(db, guild_id=GUILD_ID, owner_id=11,
                game="Другая игра", description="Проверка", capacity=2, expires_at=now+3600)
        p = await party_finder.join(db, GUILD_ID, p.id, 12)
        assert p.member_count == 2
        with pytest.raises(ValueError, match="уже состоите"):
            await party_finder.join(db, GUILD_ID, p.id, 12)
        p = await party_finder.join(db, GUILD_ID, p.id, 13)
        assert p.member_count == 3 and p.status == "full"
        with pytest.raises(ValueError):
            await party_finder.join(db, GUILD_ID, p.id, 14)
        p = await party_finder.leave(db, GUILD_ID, p.id, 13)
        assert p.member_count == 2 and p.status == "open"
        with pytest.raises(ValueError, match="Автор"):
            await party_finder.leave(db, GUILD_ID, p.id, 11)
        with pytest.raises(ValueError, match="автор"):
            await party_finder.close(db, GUILD_ID, p.id, 15)
        p = await party_finder.close(db, GUILD_ID, p.id, 11)
        assert p.status == "closed"
        with pytest.raises(ValueError):
            await party_finder.join(db, GUILD_ID, p.id, 16)
        assert await party_finder.members(db, p.id) == [11, 12]
        assert await party_finder.list_active(db, GUILD_ID) == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_five_concurrent_joins_one_last_place(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'race.db'}"
    migrate(url)
    db = Database(url)
    try:
        p = await party_finder.create(db, guild_id=GUILD_ID, owner_id=100, game="Valorant",
            description="Ищу одного", capacity=2, expires_at=int(time.time())+3600)
        results = await asyncio.gather(*(party_finder.join(db, GUILD_ID, p.id, n)
                                         for n in range(101, 106)), return_exceptions=True)
        successes = [r for r in results if not isinstance(r, BaseException)]
        assert len(successes) == 1, results
        actual = await party_finder.get(db, GUILD_ID, p.id)
        assert actual.status == "full" and actual.member_count == 2
        assert len(await party_finder.members(db, p.id)) == 2
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_expiry_and_restart_preserve_members(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'restart.db'}"
    migrate(url)
    db = Database(url)
    now = int(time.time())
    try:
        p = await party_finder.create(db, guild_id=GUILD_ID, owner_id=20, game="Terraria",
            description="Вместе", capacity=4, expires_at=now+3600)
        await party_finder.join(db, GUILD_ID, p.id, 21)
    finally:
        await db.close()
    db = Database(url)
    try:
        assert await party_finder.members(db, p.id) == [20, 21]
        assert await party_finder.expire_due(db, GUILD_ID, now=now+3601) == [p.id]
        assert (await party_finder.get(db, GUILD_ID, p.id)).status == "expired"
        with pytest.raises(ValueError):
            await party_finder.join(db, GUILD_ID, p.id, 22)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_membership_approval_outbox_and_rejoin_guard(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'membership.db'}"
    migrate(url)
    db = Database(url)
    try:
        app = await applications.submit(db, guild_id=GUILD_ID, user_id=101,
            username="user", name="Катя", gender="female", age=20, inviter="Друг")
        assert await applications.transition(db, app.id, expected=("pending",), target="approving",
            actor_id=5, reason="Одобрено", decision="approve")
        member = SimpleNamespace(guild=SimpleNamespace(id=GUILD_ID), id=101, bot=False,
            pending=False, roles=[SimpleNamespace(id=1234)],
            joined_at=datetime.fromtimestamp(app.created_at-100, tz=timezone.utc))
        # Теперь проверяется только наличие роли кандидата, а не состояние заявки.
        assert await approved_member(db, member, 5678)
        member.roles.append(SimpleNamespace(id=5678))
        assert not await approved_member(db, member, 5678)
        member.roles.pop()
        await applications.step(db, app.id, "candidate_role_status", "done")
        assert await applications.transition(db, app.id, expected=("approving",), target="approved",
            actor_id=5, reason="Доступ выдан")
        assert await approved_member(db, member, 5678)
        with sqlite3.connect(tmp_path / "membership.db") as sql:
            rows = sql.execute("SELECT id, kind FROM outbox_events").fetchall()
            assert rows == [(f"membership-approved-{app.id}", "membership.approved")]
        assert not await applications.transition(db, app.id, expected=("approving",), target="approved",
            actor_id=5, reason="Повтор")
        member.joined_at = datetime.fromtimestamp(int(time.time()) + 100, tz=timezone.utc)
        assert await approved_member(db, member, 5678)
    finally:
        await db.close()


def test_party_buttons_after_restart():
    pytest.importorskip("discord")
    from app.modules.party_finder import PartyFinder, PartyView

    v = PartyView(object(), 42)
    assert len(v.children) == 4
    assert {child.custom_id for child in v.children} == {
        "newsqad:party:42:join", "newsqad:party:42:leave",
        "newsqad:party:42:members", "newsqad:party:42:close"}
    assert v.timeout is None
    assert {x.name for x in PartyFinder.party.commands} >= {
        "create", "list", "join", "leave", "close", "settings", "configure"}


@pytest.mark.asyncio
async def test_membership_rolls_back_partial_roles_and_retries(tmp_path):
    discord = pytest.importorskip("discord")
    from app.modules.membership import Membership

    class Role:
        def __init__(self, role_id, position):
            self.id = role_id
            self.position = position

        def __le__(self, other):
            return self.position <= other.position

    primary, female, candidate = Role(1001, 10), Role(1002, 9), Role(1003, 8)

    class Member:
        id = 500
        name = "user"
        pending = False
        nick = None
        top_role = Role(5000, 3)

        def __init__(self):
            self.roles = [candidate]
            self.fail_female = True
            self.messages = []

        async def add_roles(self, role, reason):
            if role is female and self.fail_female:
                raise discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "Нет прав")
            if role not in self.roles:
                self.roles.append(role)

        async def remove_roles(self, role, reason):
            if role in self.roles:
                self.roles.remove(role)

        async def edit(self, *, nick, reason):
            self.nick = nick

        async def send(self, **kwargs):
            self.messages.append(kwargs)

    member = Member()
    guild = SimpleNamespace(
        id=GUILD_ID,
        me=SimpleNamespace(top_role=Role(9999, 100),
                           guild_permissions=SimpleNamespace(manage_roles=True)),
        get_role=lambda rid: {primary.id: primary, female.id: female,
                              candidate.id: candidate}.get(rid),
        get_member=lambda uid: member if uid == member.id else None,
    )
    url = f"sqlite+aiosqlite:///{tmp_path / 'rollback.db'}"
    migrate(url)
    db = Database(url)
    try:
        for key, value in (("member_role_id", primary.id), ("female_role_id", female.id),
                           ("candidate_role_id", candidate.id)):
            await db.set_setting(GUILD_ID, key, value, actor_id=1)
        app = await applications.submit(db, guild_id=GUILD_ID, user_id=member.id,
            username="user", name="Катя", gender="female", age=20, inviter="Не знаю")
        assert await applications.transition(db, app.id, expected=("pending",), target="approving",
            actor_id=1, reason="Принят", decision="approve")

        bot = SimpleNamespace(database=db, config=SimpleNamespace(guild_id=GUILD_ID),
                              get_guild=lambda gid: guild if gid == GUILD_ID else None)
        cog = Membership(bot)
        text = await cog.execute(app.id)
        assert "Доступ не подтверждён" in text
        assert (await applications.get_application(db, app.id)).status == "action_failed"
        assert primary not in member.roles and female not in member.roles
        assert candidate in member.roles

        member.fail_female = False
        text = await cog.execute(app.id)
        assert text.startswith("Кандидат принят")
        row = await applications.get_application(db, app.id)
        assert row.status == "approved"
        assert (row.role_status, row.female_role_status, row.candidate_role_status) == (
            "done", "done", "done")
        assert primary in member.roles and female in member.roles and candidate not in member.roles
        assert member.nick == "user | Катя"
        assert len(member.messages) == 1
        with sqlite3.connect(tmp_path / "rollback.db") as sql:
            assert sql.execute("SELECT count(*) FROM outbox_events WHERE kind='membership.approved'").fetchone()[0] == 1
    finally:
        await db.close()
