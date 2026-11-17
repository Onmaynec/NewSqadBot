"""v0.4.0: схема приватных тикетов, ограничение дублей и история."""
import sqlite3

import pytest

from app.config import GUILD_ID, VERSION
from app.core.database import migrate
from app.services.tickets import safe_text


def test_ticket_version_and_validation():
    assert VERSION == "1.5.0"
    assert safe_text(" Вопрос ", 100) == "Вопрос"
    with pytest.raises(ValueError):
        safe_text(" ", 100)
    with pytest.raises(ValueError):
        safe_text("a" * 101, 100)
    with pytest.raises(ValueError):
        safe_text("hello\x00world", 100)


def test_ticket_migration_privacy_and_uniqueness(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'tickets.db'}"
    migrate(url)
    migrate(url)
    with sqlite3.connect(tmp_path / "tickets.db") as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"tickets", "ticket_categories", "ticket_participants", "ticket_events", "ticket_panels"} <= tables
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0013_announcements"
        connection.execute("INSERT INTO ticket_categories (guild_id,key,title,description,candidate_allowed,enabled,sort_order) "
                           "VALUES (?,?,?,?,?,?,?)", (GUILD_ID, "support", "Поддержка", "Помощь", 0, 1, 0))
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO ticket_categories (guild_id,key,title,description,candidate_allowed,enabled,sort_order) "
                               "VALUES (?,?,?,?,?,?,?)", (GUILD_ID, "support", "Дубль", "Дубль", 0, 1, 0))
        connection.execute("INSERT INTO tickets (guild_id,user_id,category_id,staff_role_id,subject,description,status,opened_at,updated_at,channel_deleted) "
                           "VALUES (?,?,?,?,?,?,?,?,?,?)", (GUILD_ID, 12, 1, 90, "Вопрос", "Содержимое", "open", 100, 100, 0))
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO tickets (guild_id,user_id,category_id,staff_role_id,subject,description,status,opened_at,updated_at,channel_deleted) "
                               "VALUES (?,?,?,?,?,?,?,?,?,?)", (GUILD_ID, 12, 1, 90, "Другой", "Содержимое", "creating", 101, 101, 0))
        connection.execute("UPDATE tickets SET status='closed' WHERE id=1")
        connection.execute("INSERT INTO tickets (guild_id,user_id,category_id,staff_role_id,subject,description,status,opened_at,updated_at,channel_deleted) "
                           "VALUES (?,?,?,?,?,?,?,?,?,?)", (GUILD_ID, 12, 1, 90, "Новая заявка", "Содержимое", "open", 102, 102, 0))
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE tickets SET status='open' WHERE id=1")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO ticket_events (ticket_id,actor_id,action,details_json,created_at) "
                               "VALUES (999,1,'created','{}',100)")


@pytest.mark.asyncio
async def test_ticket_service_lifecycle(tmp_path):
    pytest.importorskip("aiosqlite")
    from app.core.database import Database
    from app.services import tickets as service

    url = f"sqlite+aiosqlite:///{tmp_path / 'tickets-async.db'}"
    migrate(url)
    db = Database(url)
    try:
        await service.seed_categories(db, GUILD_ID)
        await service.seed_categories(db, GUILD_ID)
        cats = await service.categories(db, GUILD_ID)
        assert [c.key for c in cats] == ["support", "complaint", "other"]
        assert all(not c.candidate_allowed for c in cats)
        ticket = await service.reserve(db, GUILD_ID, 111, cats[0].id, "Тема", "Вопрос",
                                       support_role_id=88)
        with pytest.raises(ValueError):
            await service.reserve(db, GUILD_ID, 111, cats[0].id, "Тема", "Вопрос",
                                  support_role_id=88)
        await service.save_control_message(db, GUILD_ID, ticket.id, 321)
        assert await service.created(db, GUILD_ID, ticket.id, 1234)
        record = await service.get(db, GUILD_ID, ticket.id)
        assert record.control_message_id == 321 and record.status == "open"
        assert await service.claim(db, GUILD_ID, ticket.id, 25)
        assert not await service.claim(db, GUILD_ID, ticket.id, 26)
        assert await service.participant(db, GUILD_ID, ticket.id, 44, 25, remove=False)
        assert await service.participants(db, ticket.id) == [44]
        assert await service.start_close(db, GUILD_ID, ticket.id, 25, "Решено")
        assert await service.finish_close(db, GUILD_ID, ticket.id)
        assert await service.reopen(db, GUILD_ID, ticket.id, 25)
        assert await service.start_close(db, GUILD_ID, ticket.id, 25, "Закрыть")
        assert await service.finish_close(db, GUILD_ID, ticket.id)
        await service.mark_deleted(db, GUILD_ID, ticket.id, 25)
        assert (await service.get(db, GUILD_ID, ticket.id)).channel_deleted
        assert not await service.reopen(db, GUILD_ID, ticket.id, 25)
        assert len(await service.history(db, ticket.id)) == 0  # Журналирование тикетов отключено
    finally:
        await db.close()
