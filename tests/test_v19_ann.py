"""v1.9.0: /ann sends into the channel where the command was invoked."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import app_commands

from app.config import GUILD_ID
from app.modules.announcements import AnnouncementModal, Announcements
from app.ui.theme import BRAND_BLUE


def context(*, approved: bool = True, embed_links: bool = True):
    guild = SimpleNamespace(id=GUILD_ID, owner_id=77, me=SimpleNamespace(id=999))
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 555
    channel.guild = guild
    channel.permissions_for.return_value = SimpleNamespace(
        view_channel=True, send_messages=True, embed_links=embed_links,
    )
    guild.get_channel = lambda cid: channel if cid == channel.id else None
    interaction = SimpleNamespace(
        guild=guild, guild_id=GUILD_ID, channel=channel,
        user=SimpleNamespace(id=42, guild_permissions=SimpleNamespace(administrator=approved)),
        response=SimpleNamespace(send_modal=AsyncMock(), send_message=AsyncMock()),
    )
    bot = SimpleNamespace(config=SimpleNamespace(guild_id=GUILD_ID, owner_ids=frozenset()))
    return Announcements(bot), interaction, channel


@pytest.mark.asyncio
async def test_ann_uses_invocation_channel_for_embed_and_text():
    cog, ctx, channel = context()
    for kind in ("embed", "text"):
        await Announcements.ann.callback(
            cog, ctx, app_commands.Choice(name=kind, value=kind),
            цвет="#001122" if kind == "embed" else None,
        )
        sent = ctx.response.send_modal.await_args.args[0]
        assert isinstance(sent, AnnouncementModal)
        assert sent.channel_id == channel.id
        assert sent.kind == kind
        assert sent.color == (0x001122 if kind == "embed" else BRAND_BLUE)
    assert ctx.response.send_modal.await_count == 2


@pytest.mark.asyncio
async def test_ann_rejects_forbidden_or_missing_target_without_creating_draft():
    cog, ctx, _ = context(approved=False)
    await Announcements.ann.callback(cog, ctx, app_commands.Choice(name="Text", value="text"))
    ctx.response.send_modal.assert_not_awaited()
    ctx.response.send_message.assert_awaited_once()

    cog, ctx, _ = context(embed_links=False)
    await Announcements.ann.callback(cog, ctx, app_commands.Choice(name="Embed", value="embed"))
    ctx.response.send_modal.assert_not_awaited()
    assert "Embed" in ctx.response.send_message.await_args.kwargs["embed"].description

    cog, ctx, _ = context()
    ctx.channel = None
    await Announcements.ann.callback(cog, ctx, app_commands.Choice(name="Text", value="text"))
    ctx.response.send_modal.assert_not_awaited()
    assert "текстовом канале" in ctx.response.send_message.await_args.kwargs["embed"].description


def test_ann_signature_no_longer_exposes_a_channel_selector():
    assert [p.name for p in Announcements.ann.parameters] == ["вид", "цвет"]
