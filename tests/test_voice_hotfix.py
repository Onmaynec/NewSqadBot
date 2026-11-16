"""v0.11.1: генератор голосовых комнат, права и восстановление после перезапуска."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from app.config import GUILD_ID, VERSION
from app.modules.voice import Voice


class FakeMember:
    def __init__(self, guild, uid, *, admin=False, bot=False):
        self.guild = guild
        self.id = uid
        self.bot = bot
        self.guild_permissions = SimpleNamespace(administrator=admin)
        self.voice = None


def make_member(guild, uid, *, admin=False, bot=False):
    return FakeMember(guild, uid, admin=admin, bot=bot)


def make_cog(db=None, owner_ids=frozenset()):
    config = SimpleNamespace(guild_id=GUILD_ID, owner_ids=owner_ids)
    fake_db = db or SimpleNamespace(get_setting=AsyncMock(return_value=None))
    bot = SimpleNamespace(config=config, database=fake_db, user=None)
    return Voice(bot)


@pytest.mark.asyncio
async def test_voice_access_only_depends_on_candidate_role(monkeypatch):
    guild = SimpleNamespace(id=GUILD_ID, owner_id=101)
    db = SimpleNamespace(get_setting=AsyncMock(return_value=777))
    cog = make_cog(db=db, owner_ids=frozenset({103}))
    membership = AsyncMock(side_effect=lambda db, member, role: member.id != 105)
    monkeypatch.setattr("app.modules.voice.approved_member", membership)

    assert await cog._approved(make_member(guild, 101))
    assert await cog._approved(make_member(guild, 102, admin=True))
    assert await cog._approved(make_member(guild, 103))
    assert await cog._approved(make_member(guild, 104))
    assert not await cog._approved(make_member(guild, 105, admin=True))
    assert not await cog._approved(make_member(guild, 101, bot=True))
    stranger = SimpleNamespace(id=9999, owner_id=101)
    assert not await cog._approved(make_member(stranger, 101))
    assert membership.await_count == 5
    assert all(call.args[2] == 777 for call in membership.await_args_list)


@pytest.mark.asyncio
async def test_voice_room_excludes_candidates_without_any_member_role():
    guild = SimpleNamespace(id=GUILD_ID, owner_id=101, default_role=object())
    me = make_member(guild, 9999)
    me.guild_permissions = SimpleNamespace(manage_channels=True, move_members=True)
    guild.me = me
    candidate = object()
    guild.get_role = lambda role_id: candidate if role_id == 777 else None
    db = SimpleNamespace(get_setting=AsyncMock(return_value=777))
    cog = make_cog(db=db)
    owner = make_member(guild, 101)
    overwrites = await cog._access_overwrites(guild, owner)
    assert overwrites[guild.default_role].connect is True
    assert overwrites[guild.default_role].view_channel is True
    assert overwrites[candidate].connect is False
    assert overwrites[candidate].view_channel is False
    assert overwrites[owner].connect is True
    assert overwrites[me].manage_channels is True
    assert len(overwrites) == 4
    guild.get_role = lambda _id: None
    with pytest.raises(ValueError, match="роль кандидата"):
        await cog._access_overwrites(guild, make_member(guild, 105))


def test_creation_permissions_are_diagnosed():
    guild = SimpleNamespace(id=GUILD_ID, owner_id=1)
    me = make_member(guild, 9999)
    me.guild_permissions = SimpleNamespace(manage_channels=True, move_members=True)
    guild.me = me
    lobby = MagicMock(spec=discord.VoiceChannel)
    category = MagicMock(spec=discord.CategoryChannel)
    lobby.permissions_for.return_value = SimpleNamespace(move_members=False)
    category.permissions_for.return_value = SimpleNamespace(manage_channels=False)
    problems = make_cog()._creation_problems(guild, lobby, category)
    assert any("канале создания" in p for p in problems)
    assert any("категории" in p for p in problems)


@pytest.mark.asyncio
async def test_on_ready_processes_users_already_in_lobby():
    guild = SimpleNamespace(id=GUILD_ID)
    lobby = MagicMock(spec=discord.VoiceChannel)
    lobby.id = 777
    member = make_member(guild, 55)
    lobby.members = [member]
    guild.get_channel = lambda _id: lobby
    cog = make_cog()
    cog._reconcile = AsyncMock()
    cog._settings = AsyncMock(return_value={"enabled": True, "lobby_channel_id": 777})
    cog._create_for = AsyncMock()
    cog.bot.get_guild = lambda _id: guild
    await cog.on_ready()
    cog._create_for.assert_awaited_once_with(member, lobby)


@pytest.mark.asyncio
async def test_voice_creation_registers_and_moves_when_admitted(monkeypatch):
    from app.services import voice as store

    guild = SimpleNamespace(id=GUILD_ID, owner_id=100)
    lobby = MagicMock(spec=discord.VoiceChannel)
    lobby.id = 777
    category = MagicMock(spec=discord.CategoryChannel)
    category.id = 888
    created = MagicMock(spec=discord.VoiceChannel)
    created.id = 999
    guild.get_channel = lambda cid: category if cid == 888 else None
    guild.create_voice_channel = AsyncMock(return_value=created)
    member = make_member(guild, 100)
    member.display_name = "Administrator"
    member.voice = SimpleNamespace(channel=lobby)
    member.move_to = AsyncMock()

    cog = make_cog()
    cog._settings = AsyncMock(return_value={
        "enabled": True, "lobby_channel_id": 777, "category_channel_id": 888,
        "max_rooms": 10, "default_limit": 0,
    })
    cog._approved = AsyncMock(return_value=True)
    cog._creation_problems = MagicMock(return_value=[])
    cog._access_overwrites = AsyncMock(return_value={})
    monkeypatch.setattr(store, "by_owner", AsyncMock(return_value=None))
    monkeypatch.setattr(store, "all_rooms", AsyncMock(return_value=[]))
    registered = AsyncMock()
    monkeypatch.setattr(store, "register", registered)
    await cog._create_for(member, lobby)
    guild.create_voice_channel.assert_awaited_once()
    registered.assert_awaited_once()
    member.move_to.assert_awaited_once_with(created, reason="NewSquad: создание временной комнаты")
    assert VERSION == "1.1.1"


@pytest.mark.asyncio
async def test_unapproved_user_does_not_create_room(monkeypatch, caplog):
    from app.services import voice as store

    guild = SimpleNamespace(id=GUILD_ID, owner_id=100)
    lobby = MagicMock(spec=discord.VoiceChannel)
    lobby.id = 777
    member = make_member(guild, 111)
    member.voice = SimpleNamespace(channel=lobby)
    cog = make_cog()
    cog._settings = AsyncMock(return_value={"enabled": True, "lobby_channel_id": 777})
    cog._approved = AsyncMock(return_value=False)
    called = AsyncMock()
    monkeypatch.setattr(store, "by_owner", called)
    with caplog.at_level("WARNING"):
        await cog._create_for(member, lobby)
    called.assert_not_awaited()
    assert "нет одобренной заявки" in caplog.text
