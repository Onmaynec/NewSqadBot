"""v1.3.0 — оформление административных панелей и упоминания ролей."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from app.config import GUILD_ID, VERSION
from app.modules.audit_logs import AuditLogs
from app.modules.tickets import ticket_embed
from app.modules.voice import RoomControls, describe_room
from app.panels.botsettings import HomeView, home_embed
from app.ui.mentions import channel_mention, role_list, role_mention, safe_display
from app.ui.theme import BRAND_BLUE, card, notice, panel_card


def test_version_and_role_formatter_are_safe():
    assert VERSION == "1.9.0"
    roles = {
        5: SimpleNamespace(id=5, mention="<@&5>"),
        10: SimpleNamespace(id=10, mention="<@&10>"),
    }
    guild = SimpleNamespace(get_role=roles.get, get_channel=lambda _: None)
    assert role_mention(roles[5]) == "<@&5>"
    assert role_mention(None, role_id=10, guild=guild) == "<@&10>"
    assert role_list(guild, [10, 5, 5]) == "<@&5>, <@&10>"
    assert role_list(guild, []) == "Нет"
    assert "недоступна" in role_mention(None, role_id=999, guild=guild)
    assert role_mention(None, role_id=None, guild=guild) == "Не назначена"
    assert "недоступен" in channel_mention(guild, 777)
    assert safe_display("Роль <@&5>, @everyone и @here").startswith("Роль <@&5>, @\u200beveryone")


@pytest.mark.asyncio
async def test_audit_role_changes_uses_mentions_not_numeric_arrays(monkeypatch):
    from app.modules import audit_logs

    guild = SimpleNamespace(
        id=GUILD_ID, get_role=lambda rid: SimpleNamespace(mention=f"<@&{rid}>", id=rid),
    )
    before = SimpleNamespace(
        guild=guild, roles=[SimpleNamespace(id=5), SimpleNamespace(id=10)], id=42,
        mention="<@42>",
    )
    after = SimpleNamespace(
        guild=guild, roles=[SimpleNamespace(id=10), SimpleNamespace(id=12)], id=42,
        mention="<@42>",
    )
    write = AsyncMock()
    monkeypatch.setattr(audit_logs, "log_event", write)
    await AuditLogs(SimpleNamespace(config=SimpleNamespace(guild_id=GUILD_ID))).on_member_update(
        before, after,
    )
    kwargs = write.await_args.kwargs
    assert "Выданы роли: <@&12>" in kwargs["details"]
    assert "Сняты роли: <@&5>" in kwargs["details"]
    assert "Участник: <@42>" in kwargs["details"]
    assert "[5]" not in kwargs["details"]
    assert "[12]" not in kwargs["details"]
    assert kwargs["action"] == "member.roles"


def test_home_is_grouped_and_keeps_all_subpages():
    bot = SimpleNamespace(config=SimpleNamespace(guild_id=GUILD_ID))
    view = HomeView(bot, owner_id=42)
    buttons = [x for x in view.children if isinstance(x, discord.ui.Button)]
    selects = [x for x in view.children if isinstance(x, discord.ui.Select)]
    assert len(buttons) == 6
    assert len(selects) == 2
    assert {x.label for x in buttons} == {
        "Состояние", "Роли", "Каналы", "Объявления", "Диагностика", "Все настройки",
    }
    assert len({opt.value for select in selects for opt in select.options}) == 13
    assert "Центр управления" in home_embed().title
    assert "Быстрый доступ" in home_embed().description


@pytest.mark.asyncio
async def test_home_dropdown_routes_to_original_callback():
    bot = SimpleNamespace(config=SimpleNamespace(guild_id=GUILD_ID))
    view = HomeView(bot, owner_id=42)
    select = next(
        s for s in view.children
        if isinstance(s, discord.ui.Select)
        and any(o.value == "Профили" for o in s.options)
    )
    select._values = ["Профили"]
    interaction = SimpleNamespace(
        response=SimpleNamespace(edit_message=AsyncMock()),
    )
    await select.callback(interaction)
    response = interaction.response.edit_message.await_args.kwargs
    assert response["embed"].title.endswith("Профили")
    assert response["view"] is not None


def test_role_card_ticket_card_and_room_card_have_sections():
    embed = panel_card("Название", "Краткий текст",
                       ("Права", "<@&5>"), ("Доступ", "Открыт"))
    assert "**Права**" in embed.description
    assert "\n\n**Доступ**" in embed.description
    assert embed.color.value == BRAND_BLUE
    assert card("Тест").color.value == BRAND_BLUE
    assert "@everyone" not in notice("@everyone тест", kind="warning").description

    ticket = SimpleNamespace(
        id=12, subject="Проблема с ролями", description="Нужна помощь",
        category_id=1, user_id=42, status="open", assignee_id=None,
        close_reason=None,
    )
    category = SimpleNamespace(title="Поддержка")
    t = ticket_embed(ticket, category)
    assert "**Описание**" in t.description
    assert any(f.name == "Статус" and f.value == "Открыт" for f in t.fields)
    room = SimpleNamespace(
        name="Тестовая комната", channel_id=123, owner_id=42,
        user_limit=5, locked=False, hidden=True,
    )
    v = describe_room(room)
    assert "**Доступ и приватность**" in v.description
    assert "скрытая" in v.description
    controls = RoomControls(SimpleNamespace(), 123)
    rows = {btn.label: btn.row for btn in controls.children}
    assert rows["Управление входом"] == 0
    assert rows["Название"] == 1
    assert rows["Удалить комнату"] == 2


@pytest.mark.asyncio
async def test_audit_embeds_keep_role_mention_without_ping(tmp_path):
    from app.core.database import Database, migrate
    from app.services.audit import log_event

    url = f"sqlite+aiosqlite:///{tmp_path / 'audit.db'}"
    migrate(url)
    db = Database(url)
    channel = MagicMock(spec=discord.TextChannel)
    channel.send = AsyncMock()
    guild = SimpleNamespace(get_channel=lambda _: channel)
    bot = SimpleNamespace(
        config=SimpleNamespace(guild_id=GUILD_ID),
        database=db, get_guild=lambda _: guild,
    )
    original = db.get_setting
    db.get_setting = AsyncMock(return_value=111)
    try:
        await log_event(
            bot, channel_setting="audit_channel_id", title="Снята роль",
            details="Участник: <@42>\nРоль: <@&5>\nКомментарий: @everyone",
            actor_id=42, action="roles.revoke", target_id=42,
        )
        embed = channel.send.await_args.kwargs["embed"]
        assert "<@&5>" in embed.description
        assert "<@42>" in embed.description
        assert "@everyone" not in embed.description
        assert channel.send.await_args.kwargs["allowed_mentions"].everyone is False
        assert any(field.name == "Инициатор" for field in embed.fields)
        assert embed.color.value == BRAND_BLUE
    finally:
        db.get_setting = original
        await db.close()
