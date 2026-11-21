"""v1.1.0: объявления, двухпольные формы и интерактивная справка Discord."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import app_commands

from app.config import GUILD_ID, VERSION
from app.modules.announcements import (
    AnnouncementModal,
    Announcements,
    build_announcement,
    parse_color,
)
from app.modules.help_center import (
    HelpCenter,
    HelpEntry,
    HelpView,
    build_pages,
    collect_entries,
    page_embed,
)
from app.ui.theme import BRAND_BLUE


def test_version_and_command_registration():
    assert VERSION == "2.1.0"
    assert Announcements.ann.name == "ann"
    assert HelpCenter.help.name == "help"
    options = {p.name for p in Announcements.ann.parameters}
    assert options == {"вид", "цвет"}


def test_color_validation_default_and_explicit():
    assert parse_color(None) == BRAND_BLUE
    assert parse_color("") == BRAND_BLUE
    assert parse_color(" #aB30fe ") == 0xAB30FE
    assert parse_color("012345") == 0x012345
    for invalid in ("blue", "fff", "#ffffff00", "#GGGGGG", "<@everyone>"):
        with pytest.raises(ValueError, match="формате"):
            parse_color(invalid)


def test_announcement_payload_and_safe_mentions():
    embedded = build_announcement(
        "embed", "NewSqadBot v1.1.0", "Новости\n\n- @everyone добавлены команды"
    )
    e = embedded["embed"]
    assert e.title == "NewSqadBot v1.1.0"
    assert e.color.value == BRAND_BLUE
    assert "@everyone" not in e.description
    assert "объявление" in e.author.name.lower()
    assert embedded["allowed_mentions"].everyone is False

    plain = build_announcement("text", "Новое обновление", "**Пункт 1**\n@here исправлено")
    assert plain["content"].startswith("## Новое обновление")
    assert "@here" not in plain["content"]
    assert plain["allowed_mentions"].everyone is False
    assert len(plain["content"]) < 2000

    custom = build_announcement("embed", "Изменения", "Версия 1.1", 0xFF0099)
    assert custom["embed"].color.value == 0xFF0099


def test_modal_has_exactly_two_inputs_and_length_limits():
    cog = Announcements(SimpleNamespace(config=SimpleNamespace(owner_ids=frozenset())))
    embed = AnnouncementModal(cog, channel_id=7, kind="embed", color=BRAND_BLUE)
    plain = AnnouncementModal(cog, channel_id=7, kind="text", color=BRAND_BLUE)
    assert [x.text for x in embed.children] == ["Заголовок", "Текст объявления"]
    assert len(embed.children) == len(plain.children) == 2
    assert embed.body.max_length == 3500
    assert plain.body.max_length == 1750


def test_empty_and_oversized_announcements_rejected():
    for kind, title, body in (
        ("embed", " ", "Описание"),
        ("embed", "Заголовок\nподмена", "Описание"),
        ("text", "Заголовок", "x" * 1751),
        ("embed", "Заголовок", "x" * 3501),
    ):
        with pytest.raises(ValueError):
            build_announcement(kind, title, body)


def make_announcement_context(*, admin: bool = True):
    member = SimpleNamespace(id=42, guild_permissions=SimpleNamespace(administrator=admin))
    bot_member = SimpleNamespace(id=999)
    guild = SimpleNamespace(id=GUILD_ID, me=bot_member, owner_id=12345)
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 1122
    channel.guild = guild
    channel.mention = "<#1122>"
    channel.permissions_for.return_value = SimpleNamespace(
        view_channel=True, send_messages=True, embed_links=True,
    )
    channel.send = AsyncMock(return_value=SimpleNamespace(
        id=3344, jump_url="https://discord.com/channels/1439347293076586638/1122/3344",
    ))
    guild.get_channel = lambda cid: channel if cid == channel.id else None
    interaction = SimpleNamespace(
        id=5566, guild=guild, guild_id=GUILD_ID, user=member,
        response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )
    bot = SimpleNamespace(config=SimpleNamespace(
        guild_id=GUILD_ID, owner_ids=frozenset(),
    ))
    return Announcements(bot), interaction, channel


@pytest.mark.asyncio
async def test_publish_rechecks_permissions_audits_and_confirms(monkeypatch):
    from app.modules import announcements

    cog, interaction, channel = make_announcement_context()
    audit = AsyncMock()
    monkeypatch.setattr(announcements, "log_event", audit)
    await cog.publish(
        interaction, channel_id=1122, kind="embed", color=BRAND_BLUE,
        title="Версия 1.1", body="Описание обновления",
    )
    interaction.response.defer.assert_awaited_once()
    channel.send.assert_awaited_once()
    kwargs = channel.send.await_args.kwargs
    assert kwargs["embed"].color.value == BRAND_BLUE
    assert kwargs["allowed_mentions"].everyone is False
    audit.assert_awaited_once()
    assert audit.await_args.kwargs["action"] == "ann.published"
    assert "опубликовано" in interaction.followup.send.await_args.kwargs["embed"].description.lower()


@pytest.mark.asyncio
async def test_publish_does_not_send_after_revoking_admin_role(monkeypatch):
    from app.modules import announcements

    cog, interaction, channel = make_announcement_context(admin=False)
    audit = AsyncMock()
    monkeypatch.setattr(announcements, "log_event", audit)
    await cog.publish(
        interaction, channel_id=1122, kind="embed", color=BRAND_BLUE,
        title="Заголовок", body="Нельзя публиковать",
    )
    channel.send.assert_not_awaited()
    audit.assert_not_awaited()
    interaction.response.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_publish_blocks_if_bot_loses_channel_rights():
    cog, interaction, channel = make_announcement_context()
    channel.permissions_for.return_value = SimpleNamespace(
        view_channel=True, send_messages=True, embed_links=False,
    )
    await cog.publish(
        interaction, channel_id=1122, kind="embed", color=BRAND_BLUE,
        title="Заголовок", body="Должно быть отклонено",
    )
    channel.send.assert_not_awaited()
    assert "Embed" in interaction.response.send_message.await_args.kwargs["embed"].description


@pytest.mark.asyncio
async def test_publish_logs_audit_failure_but_never_duplicates(monkeypatch):
    from app.modules import announcements

    cog, interaction, channel = make_announcement_context()
    monkeypatch.setattr(
        announcements, "log_event", AsyncMock(side_effect=RuntimeError("DB unavailable")),
    )
    await cog.publish(
        interaction, channel_id=1122, kind="text", color=BRAND_BLUE,
        title="Новости", body="Объявление",
    )
    channel.send.assert_awaited_once()
    assert interaction.followup.send.await_count == 1


def test_help_collects_real_registered_nested_commands():
    async def callback(interaction: discord.Interaction) -> None:
        return None

    commands_list = [
        app_commands.Command(name="help", description="Справка", callback=callback),
        app_commands.Command(name="ann", description="Объявления", callback=callback),
    ]
    group = app_commands.Group(name="suggest", description="Предложения")
    group.add_command(app_commands.Command(
        name="create", description="Предложить идею", callback=callback,
    ))
    nested = app_commands.Group(name="manage", description="Управление")
    nested.add_command(app_commands.Command(
        name="status", description="Статус идеи", callback=callback,
    ))
    group.add_command(nested)
    commands_list.append(group)

    class Tree:
        def get_commands(self, *, guild):
            assert guild.id == GUILD_ID
            return commands_list

    entries = collect_entries(Tree(), GUILD_ID)
    assert {x.name for x in entries} == {
        "/help", "/ann", "/suggest create", "/suggest manage status",
    }
    assert next(x.description for x in entries if x.name == "/suggest create") == "Предложить идею"


def test_help_pagination_select_navigation_and_blue_embed():
    entries = [HelpEntry(f"/suggest command{i}", f"Описание команды {i}") for i in range(19)]
    entries += [HelpEntry("/ann", "Создание объявлений"), HelpEntry("/help", "Справка")]
    pages = build_pages(entries)
    assert [len(p.entries) for p in pages if p.category == "suggest"] == [8, 8, 3]
    assert sum(len(p.entries) for p in pages) == 21
    assert pages[0].category == "help"
    for i, page in enumerate(pages):
        rendered = page_embed(pages, i)
        assert rendered.color.value == BRAND_BLUE
        for command in page.entries:
            assert command.name in rendered.description
        assert f"Страница {i + 1}" in rendered.footer.text
    view = HelpView(pages, owner_id=42)
    assert view.previous.disabled
    assert not view.next_page.disabled
    assert any(isinstance(x, discord.ui.Select) for x in view.children)
    view.page = len(pages) - 1
    view.sync()
    assert view.next_page.disabled


@pytest.mark.asyncio
async def test_help_page_rejects_other_users_and_guilds():
    pages = build_pages([HelpEntry("/help", "Справка"), HelpEntry("/ann", "Объявления")])
    view = HelpView(pages, owner_id=42)
    interaction = SimpleNamespace(
        guild_id=GUILD_ID, user=SimpleNamespace(id=999),
        response=SimpleNamespace(send_message=AsyncMock(), is_done=lambda: False),
        followup=SimpleNamespace(send=AsyncMock()),
    )
    assert not await view.interaction_check(interaction)
    assert interaction.response.send_message.await_count == 1
    interaction.user.id = 42
    assert await view.interaction_check(interaction)
    interaction.guild_id = 999
    assert not await view.interaction_check(interaction)
