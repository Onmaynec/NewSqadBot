import sqlite3

from app.core.database import migrate


def test_core_migration_idempotent(tmp_path):
    db = tmp_path / "newsqad.db"
    url = f"sqlite+aiosqlite:///{db}"
    migrate(url)
    migrate(url)
    with sqlite3.connect(db) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"guild_settings", "system_audit", "scheduled_jobs", "outbox_events", "event_receipts"} <= tables
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0007_quests_achievements"
