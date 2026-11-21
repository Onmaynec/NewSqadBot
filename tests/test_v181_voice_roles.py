"""v1.8.1: Discord voice lifecycle, candidate gate and role API failures.

These tests use simulated Discord objects. A manual staging-guild check is still
required before claiming that live Discord permissions work end to end.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from app.config import GUILD_ID
from app.modules.roles import Roles
from app.modules.voice import Voice
from app.services import voice as voice_store
from app.services.role_rules import is_approved


def fake_member(guild, user_id: int, *, roles=()):
    member = MagicMock(spec=discord.Member)
    member.id = user_id
    member.guild = guild
    member.roles = [SimpleNamespace(id=role_id) for role_id in roles]
    member.bot = False
    member.display_name = f"User {user_id}"
    member.guild_permissions = SimpleNamespace(administrator=False)
    member.move_to = AsyncMock()
    member.add_roles = AsyncMock()
    member.remove_roles = AsyncMock()
    return member


def forbidden():
    return discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "Missing Permissions")


@pytest.mark.asyncio
async def test_voice_join_manage_handoff_and_delete(monkeypatch):
    """Entry to generator -> creation -> owner controls -> handoff -> deletion."""
    guild = SimpleNamespace(id=GUILD_ID, owner_id=999, default_role=object())
    lobby = MagicMock(spec=discord.VoiceChannel)
    lobby.id = 100
    category = MagicMock(spec=discord.CategoryChannel)
    category.id = 200
    channel = MagicMock(spec=discord.VoiceChannel)
    channel.id = 300
    channel.members = []
    channel.edit = AsyncMock()
    channel.delete = AsyncMock()
    channel.set_permissions = AsyncMock()
    owner = fake_member(guild, 41)
    successor = fake_member(guild, 42)
    owner.voice = SimpleNamespace(channel=lobby)
    successor.voice = SimpleNamespace(channel=None)
    members = {41: owner, 42: successor}
    channels = {100: lobby, 200: category, 300: channel}
    guild.get_channel = channels.get
    guild.get_member = members.get
    guild.create_voice_channel = AsyncMock(return_value=channel)

    async def move_owner(destination, *, reason):
        owner.voice.channel = destination
        channel.members = [owner]
    owner.move_to.side_effect = move_owner
    records = {}

    def by_owner(_db, _guild_id, user_id):
        return next((r for r in records.values() if r.owner_id == user_id), None)

    def by_channel(_db, _guild_id, channel_id):
        return records.get(channel_id)

    def register(_db, **kwargs):
        records[kwargs["channel_id"]] = SimpleNamespace(
            id=1, channel_id=kwargs["channel_id"], owner_id=kwargs["owner_id"],
            locked=False, hidden=False, user_limit=kwargs["user_limit"],
            name=kwargs["name"],
        )

    def transfer(_db, _gid, channel_id, new_id):
        records[channel_id].owner_id = new_id

    def rename(_db, _gid, channel_id, *, name):
        records[channel_id].name = name

    def remove(_db, _gid, channel_id):
        records.pop(channel_id, None)

    monkeypatch.setattr(voice_store, "by_owner", AsyncMock(side_effect=by_owner))
    monkeypatch.setattr(voice_store, "by_channel", AsyncMock(side_effect=by_channel))
    monkeypatch.setattr(voice_store, "all_rooms", AsyncMock(side_effect=lambda *_: list(records.values())))
    monkeypatch.setattr(voice_store, "register", AsyncMock(side_effect=register))
    monkeypatch.setattr(voice_store, "transfer", AsyncMock(side_effect=transfer))
    monkeypatch.setattr(voice_store, "rename", AsyncMock(side_effect=rename))
    monkeypatch.setattr(voice_store, "remove", AsyncMock(side_effect=remove))
    monkeypatch.setattr(voice_store, "update_state", AsyncMock())

    db = SimpleNamespace(get_setting=AsyncMock(return_value=777))
    bot = SimpleNamespace(database=db, config=SimpleNamespace(guild_id=GUILD_ID, owner_ids=frozenset()),
                          user=None)
    cog = Voice(bot)
    cog._settings = AsyncMock(return_value={
        "enabled": True, "lobby_channel_id": 100, "category_channel_id": 200,
        "default_limit": 0, "max_rooms": 10,
    })
    cog._creation_problems = MagicMock(return_value=[])
    cog._access_overwrites = AsyncMock(return_value={})

    await cog.on_voice_state_update(owner, SimpleNamespace(channel=None), SimpleNamespace(channel=lobby))
    assert records[300].owner_id == 41
    owner.move_to.assert_awaited_once_with(channel, reason="NewSquad: создание временной комнаты")

    interaction = SimpleNamespace(guild=guild, guild_id=GUILD_ID, user=owner)
    assert "Готово" in await cog.rename_room(interaction, 300, "Команда Альфа")
    assert records[300].name == "Команда Альфа"
    assert "Готово" in await cog.limit_room(interaction, 300, 5)
    voice_store.update_state.assert_awaited()
    assert await cog.visibility(interaction, 300, "locked", True) == "Готово. Параметры комнаты обновлены."

    # A successor without the candidate role can inherit the room.
    owner.voice.channel = None
    successor.voice.channel = channel
    channel.members = [successor]
    assert is_approved(successor, 777)
    await cog._handoff(guild, 300)
    assert records[300].owner_id == 42
    assert channel.set_permissions.await_count >= 2

    successor_interaction = SimpleNamespace(guild=guild, guild_id=GUILD_ID, user=successor)
    assert await cog.delete_room(successor_interaction, 300) == "Готово. Комната удалена."
    channel.delete.assert_awaited_once()
    assert records == {}


@pytest.mark.asyncio
async def test_voice_discord_forbidden_does_not_lose_room_state(monkeypatch):
    guild = SimpleNamespace(id=GUILD_ID)
    channel = MagicMock(spec=discord.VoiceChannel)
    channel.id = 300
    channel.edit = AsyncMock(side_effect=forbidden())
    guild.get_channel = lambda _id: channel
    owner = fake_member(guild, 41)
    db = SimpleNamespace(get_setting=AsyncMock(return_value=777))
    bot = SimpleNamespace(database=db, config=SimpleNamespace(guild_id=GUILD_ID, owner_ids=frozenset()),
                          user=None)
    cog = Voice(bot)
    room = SimpleNamespace(id=1, channel_id=300, owner_id=41)
    monkeypatch.setattr(voice_store, "by_channel", AsyncMock(return_value=room))
    recorded = AsyncMock()
    monkeypatch.setattr(voice_store, "rename", recorded)
    interaction = SimpleNamespace(guild=guild, guild_id=GUILD_ID, user=owner)
    assert "Manage Channels" in await cog.rename_room(interaction, 300, "Новое имя")
    recorded.assert_not_awaited()


@pytest.mark.asyncio
async def test_role_autogrant_is_idempotent_and_denied_for_candidates():
    guild = SimpleNamespace(id=GUILD_ID)
    role = MagicMock(spec=discord.Role)
    role.id = 900
    member = fake_member(guild, 41)
    guild.get_role = lambda rid: role if rid == 900 else None
    guild.fetch_member = AsyncMock(return_value=member)
    bot = SimpleNamespace(
        config=SimpleNamespace(guild_id=GUILD_ID),
        database=SimpleNamespace(get_setting=AsyncMock(
            side_effect=lambda _gid, key, default=None:
                777 if key == "candidate_role_id" else [900])),
        get_guild=lambda gid: guild if gid == GUILD_ID else None,
    )
    cog = Roles.__new__(Roles)
    cog.bot = bot
    cog._role_allowed = AsyncMock(return_value=True)

    await cog._approved({"guild_id": GUILD_ID, "user_id": 41})
    member.add_roles.assert_awaited_once_with(role, reason="[NSB-AUTOROLE] Одобрение заявки")
    member.roles.append(role)
    await cog._approved({"guild_id": GUILD_ID, "user_id": 41})
    assert member.add_roles.await_count == 1

    # Even an admin retaining the candidate role is not approved.
    member.roles.append(SimpleNamespace(id=777))
    await cog._approved({"guild_id": GUILD_ID, "user_id": 41})
    assert member.add_roles.await_count == 1
    member.roles = [SimpleNamespace(id=777)]
    assert not is_approved(member, 777)


@pytest.mark.asyncio
async def test_role_autogrant_propagates_discord_forbidden_for_worker_retry():
    guild = SimpleNamespace(id=GUILD_ID)
    role = MagicMock(spec=discord.Role)
    role.id = 900
    member = fake_member(guild, 41)
    member.add_roles.side_effect = forbidden()
    guild.get_role = lambda _id: role
    guild.fetch_member = AsyncMock(return_value=member)
    bot = SimpleNamespace(
        config=SimpleNamespace(guild_id=GUILD_ID),
        database=SimpleNamespace(get_setting=AsyncMock(
            side_effect=lambda _gid, key, default=None:
                777 if key == "candidate_role_id" else [900])),
        get_guild=lambda _gid: guild,
    )
    cog = Roles.__new__(Roles)
    cog.bot = bot
    cog._role_allowed = AsyncMock(return_value=True)
    with pytest.raises(discord.Forbidden):
        await cog._approved({"guild_id": GUILD_ID, "user_id": 41})
    member.add_roles.assert_awaited_once()
