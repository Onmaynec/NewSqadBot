"""Проверки настройки категории Discord для приватных тикетов."""
from pathlib import Path

import pytest

from app.config import VERSION


def test_ticket_category_is_exposed_in_settings():
    source = (Path(__file__).resolve().parents[1] / "app/panels/botsettings.py").read_text(encoding="utf-8")
    assert VERSION == "0.13.0"
    assert '("ticket_category_channel_id", "Категория приватных тикетов")' in source
    assert 'discord.ChannelType.category if self.category_slot else' in source
    assert 'view=ValuePickerView(self.client, self.owner_id, "channel", "ticket_category_channel_id")' in source


def test_tickets_preflight_before_modal():
    source = (Path(__file__).resolve().parents[1] / "app/modules/tickets.py").read_text(encoding="utf-8")
    before_categories = source.split("class TicketOpenView")[1].split("class TicketCategorySelect")[0]
    assert 'get_setting(interaction.guild_id, "ticket_category_channel_id")' in before_categories
    assert 'isinstance(parent, discord.CategoryChannel)' in before_categories
    assert before_categories.index("if not isinstance(parent, discord.CategoryChannel)") < before_categories.index(
        "await interaction.followup.send(\"Выберите категорию обращения:\""
    )


def test_category_picker_uses_discord_category_type():
    discord = pytest.importorskip("discord")
    from app.panels.botsettings import ChannelPicker
    category_picker = ChannelPicker(None, 1, "ticket_category_channel_id")
    text_picker = ChannelPicker(None, 1, "ticket_panel_channel_id")
    assert category_picker.category_slot
    assert not text_picker.category_slot
    assert category_picker.channel_types == [discord.ChannelType.category]
    assert text_picker.channel_types == [discord.ChannelType.text]
