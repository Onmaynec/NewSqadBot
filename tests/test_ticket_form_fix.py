"""Защита от регрессии формы тикетов."""
from pathlib import Path


def test_short_description_is_valid():
    source = (Path(__file__).resolve().parents[1] / "app/modules/tickets_views.py").read_text(encoding="utf-8")
    assert "min_length=3, max_length=1500" in source
    assert "await interaction.response.send_modal(NewTicketModal(self.cog, category_id))" in source
    assert "await interaction.response.defer(ephemeral=True, thinking=True)" in source
