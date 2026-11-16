"""v1.1.1: авторизация по роли кандидата, без остальных ролей и истории заявок."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from app.config import GUILD_ID, VERSION
from app.modules.profiles import Profiles
from app.services.membership_access import approved_member
from app.services.role_rules import is_approved

CANDIDATE = 999111
MEMBER = 111222
ADMIN = 333444


def member(*role_ids, bot=False, pending=False, administrator=False):
    guild = SimpleNamespace(id=GUILD_ID, owner_id=98765)
    user = MagicMock(spec=discord.Member)
    user.id = 12345
    user.bot = bot
    user.guild = guild
    user.roles = [SimpleNamespace(id=rid) for rid in role_ids]
    user.pending = pending
    user.guild_permissions = SimpleNamespace(administrator=administrator)
    return user


@pytest.mark.asyncio
async def test_candidate_role_exclusively_decides_access():
    db = MagicMock()
    for roles in ((), (MEMBER,), (ADMIN,), (MEMBER, ADMIN)):
        user = member(*roles)
        assert is_approved(user, CANDIDATE)
        assert await approved_member(db, user, CANDIDATE)
    for roles in ((CANDIDATE,), (CANDIDATE, MEMBER), (CANDIDATE, ADMIN)):
        user = member(*roles, administrator=True)
        assert not is_approved(user, CANDIDATE)
        assert not await approved_member(db, user, CANDIDATE)
    # Никакие состояния Discord screening и история заявок не учитываются.
    assert await approved_member(db, member(pending=True), CANDIDATE)
    assert not await approved_member(db, member(bot=True), CANDIDATE)
    assert not await approved_member(db, member(), None)
    db.sessions.assert_not_called()
    assert VERSION == "1.1.1"


@pytest.mark.asyncio
async def test_profile_edit_access_for_member_without_member_role():
    settings = SimpleNamespace(
        get_setting=AsyncMock(return_value=CANDIDATE),
    )
    bot = SimpleNamespace(
        config=SimpleNamespace(guild_id=GUILD_ID), database=settings,
    )
    cog = Profiles(bot)
    bare_member = member()
    assert await cog.member_ok(bare_member)
    assert await cog.member_ok(member(ADMIN))
    assert await cog.member_ok(member(MEMBER))
    assert not await cog.member_ok(member(CANDIDATE))
    assert not await cog.member_ok(member(CANDIDATE, MEMBER))


@pytest.mark.asyncio
async def test_role_removal_immediately_unlocks_without_database_changes():
    db = MagicMock()
    user = member(CANDIDATE, MEMBER)
    assert not await approved_member(db, user, CANDIDATE)
    user.roles = [role for role in user.roles if role.id != CANDIDATE]
    assert await approved_member(db, user, CANDIDATE)
    user.roles.append(SimpleNamespace(id=CANDIDATE))
    assert not await approved_member(db, user, CANDIDATE)


@pytest.mark.asyncio
async def test_candidate_id_must_be_configured():
    db = MagicMock()
    user = member()
    assert not await approved_member(db, user, None)
    assert not await approved_member(db, user, 0)
