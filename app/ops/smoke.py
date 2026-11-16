"""Smoke без токена: миграция пустой базы, повторный запуск и DB ping."""
from __future__ import annotations

import asyncio
import importlib
import sqlite3
import tempfile
from pathlib import Path

from app.core.catalog import MODULES
from app.core.database import Database, migrate


async def _check_database(url: str) -> None:
    database = Database(url)
    try:
        assert await database.ping(), "SQLAlchemy не отвечает"
    finally:
        await database.close()


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="newsqad-smoke-") as folder:
        dbpath = Path(folder) / "smoke.db"
        url = f"sqlite+aiosqlite:///{dbpath}"
        migrate(url)
        migrate(url)  # Проверка повторного применения миграций.
        with sqlite3.connect(dbpath) as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
            assert not conn.execute("PRAGMA foreign_key_check").fetchall()
            assert conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        asyncio.run(_check_database(url))
        for module in MODULES:
            importlib.import_module(f"app.modules.{module.key}")
        for extension in ("announcements", "help_center"):
            importlib.import_module(f"app.modules.{extension}")
    print(f"Smoke пройден: SQLite + Alembic + {len(MODULES)} модулей + /ann + /help")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
