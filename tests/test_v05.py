"""v0.5.0: удаление закрытых тикетов, цветовая схема и сводка настроек."""
import logging
import sqlite3

import pytest

from app.config import GUILD_ID, VERSION
from app.core.database import migrate


def test_version_and_ticket_storage_tables(tmp_path):
    assert VERSION == "0.8.0"
    url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    migrate(url)
    with sqlite3.connect(tmp_path / "test.db") as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0007_quests_achievements"


def test_console_formatter_redacts_token(monkeypatch):
    from app.core.logging_setup import DockerFormatter, SecretFilter

    monkeypatch.setenv("DISCORD_TOKEN", "secret_example_token")
    record = logging.LogRecord(name="app.modules.tickets", level=logging.ERROR, pathname="tickets.py",
                               lineno=19, msg="Ошибка Discord: secret_example_token", args=(), exc_info=None)
    assert SecretFilter().filter(record)
    result = DockerFormatter("%(name)s:%(lineno)d | %(message)s").format(record)
    assert "[REDACTED]" in result
    assert "secret_example_token" not in result
    assert "app.modules.tickets:19" in result


@pytest.mark.asyncio
async def test_closed_ticket_erases_data_without_history(tmp_path):
    pytest.importorskip("aiosqlite")
    from app.core.database import Database
    from app.services import tickets as store

    url = f"sqlite+aiosqlite:///{tmp_path / 'delete.db'}"
    migrate(url)
    db = Database(url)
    try:
        await store.seed_categories(db, GUILD_ID)
        cats = await store.categories(db, GUILD_ID)
        ticket = await store.reserve(db, GUILD_ID, 123, cats[0].id, "Тест", "Приватное содержание",
                                     support_role_id=987, max_open=3)
        await store.save_control_message(db, GUILD_ID, ticket.id, 111)
        assert await store.created(db, GUILD_ID, ticket.id, 222)
        assert await store.participant(db, GUILD_ID, ticket.id, 555, 999, remove=False)
        assert await store.claim(db, GUILD_ID, ticket.id, 999)
        assert await store.history(db, ticket.id) == []
        assert await store.start_close(db, GUILD_ID, ticket.id, 123, "Закрыто")
        assert await store.erase_ticket(db, GUILD_ID, ticket.id)
        assert await store.get(db, GUILD_ID, ticket.id) is None
        assert await store.participants(db, ticket.id) == []
        assert await store.history(db, ticket.id) == []
        with sqlite3.connect(tmp_path / "delete.db") as con:
            assert con.execute("SELECT COUNT(*) FROM ticket_events").fetchone()[0] == 0
            assert con.execute("SELECT COUNT(*) FROM ticket_participants").fetchone()[0] == 0
            assert con.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 0
    finally:
        await db.close()


def test_discord_views_and_dashboard():
    discord = pytest.importorskip("discord")
    from app.panels.settings_overview import PAGE_OPTIONS, SettingsOverviewView
    from app.ui.theme import BRAND_GREEN, card

    assert BRAND_GREEN == 0x4CCE91
    assert card("test").color == discord.Color(BRAND_GREEN)
    assert {"summary", "roles", "channels", "tickets", "leveling", "profiles", "modules", "all", "economy", "quests", "achievements"} <= {p[0] for p in PAGE_OPTIONS}
    class Config:
        owner_ids = frozenset()
    class Bot:
        config = Config()
    view = SettingsOverviewView(Bot(), owner_id=12)
    assert len(view.children) == 5  # section select + 4 controls
    assert view.selector.placeholder == "Выберите раздел настроек"
    assert view.previous.disabled
    assert view.next.disabled
