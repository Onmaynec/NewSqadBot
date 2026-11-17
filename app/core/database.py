"""Доступ к SQLite через SQLAlchemy async и реальные Alembic-миграции."""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import event, select, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import AuditEntry, GuildSetting

log = logging.getLogger(__name__)


def migrate(database_url: str) -> None:
    """Запускается до инициализации async engine; Alembic использует sync sqlite драйвер."""
    if not database_url.startswith("sqlite+aiosqlite:///"):
        raise ValueError("Неподдерживаемый драйвер БД")
    cfg = AlembicConfig(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", database_url.replace("sqlite+aiosqlite", "sqlite", 1))
    command.upgrade(cfg, "head")
    # WAL снижает конкуренцию чтения и записи между веб-панелью и Discord
    # обработчиками. Устанавливается однократно, ДО запуска фоновых задач.
    db_path = make_url(database_url).database
    if db_path and db_path != ":memory:":
        with sqlite3.connect(db_path, timeout=15) as connection:
            mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()
            if not mode or mode[0].lower() != "wal":
                raise RuntimeError("SQLite не удалось перевести в WAL; проверьте filesystem/volume.")


class Database:
    def __init__(self, url: str):
        self.engine = create_async_engine(url, pool_pre_ping=True, connect_args={"timeout": 15})
        # sqlite+aiosqlite возвращает адаптированный DBAPI, события connect
        # выполняются при создании каждого физического соединения.
        @event.listens_for(self.engine.sync_engine, "connect")
        def configure_connection(connection, _record) -> None:
            cursor = connection.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys = ON")
                cursor.execute("PRAGMA busy_timeout = 15000")
            finally:
                cursor.close()

        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        started = time.monotonic()
        try:
            async with self.sessions() as session:
                async with session.begin():
                    yield session
        finally:
            duration = time.monotonic() - started
            if duration >= 2:
                log.warning("Долгая транзакция SQLite: %.2f сек", duration)

    async def get_setting(self, guild_id: int, key: str, default: Any = None) -> Any:
        async with self.sessions() as session:
            row = await session.scalar(
                select(GuildSetting.value_json).where(GuildSetting.guild_id == guild_id, GuildSetting.key == key)
            )
            return default if row is None else json.loads(row)

    async def set_setting(self, guild_id: int, key: str, value: Any, *, actor_id: int) -> None:
        if len(key) > 120:
            raise ValueError("Слишком длинный ключ настройки")
        payload = json.dumps(value, ensure_ascii=False)
        async with self.session() as session:
            stmt = sqlite_insert(GuildSetting).values(guild_id=guild_id, key=key, value_json=payload)
            await session.execute(stmt.on_conflict_do_update(
                index_elements=[GuildSetting.guild_id, GuildSetting.key],
                set_={"value_json": payload},
            ))
            session.add(AuditEntry(
                guild_id=guild_id, actor_id=actor_id,
                action="settings.updated", data_json=json.dumps({"key": key}, ensure_ascii=False),
                created_at=int(time.time()),
            ))


    async def set_settings(self, guild_id: int, values: dict[str, Any], *, actor_id: int) -> None:
        """Один atomic commit для раздела веб-панели, включая audit каждой настройки."""
        if not 1 <= len(values) <= 80:
            raise ValueError("Изменений должно быть от 1 до 80.")
        prepared: dict[str, str] = {}
        for key, value in values.items():
            if not isinstance(key, str) or not 1 <= len(key) <= 120:
                raise ValueError("Недопустимый ключ настройки.")
            prepared[key] = json.dumps(value, ensure_ascii=False)
        now = int(time.time())
        async with self.session() as session:
            for key, payload in prepared.items():
                statement = sqlite_insert(GuildSetting).values(
                    guild_id=guild_id, key=key, value_json=payload)
                await session.execute(statement.on_conflict_do_update(
                    index_elements=[GuildSetting.guild_id, GuildSetting.key],
                    set_={"value_json": payload},
                ))
                session.add(AuditEntry(
                    guild_id=guild_id, actor_id=actor_id,
                    action="settings.updated",
                    data_json=json.dumps({"key": key, "source": "local_web"}, ensure_ascii=False),
                    created_at=now,
                ))

    async def ping(self) -> bool:
        async with self.sessions() as session:
            await session.execute(text("SELECT 1"))
        return True

    async def check_integrity(self) -> bool:
        """Проверка SQLite после миграции: страницы БД и внешние ключи."""
        async with self.sessions() as session:
            result = await session.execute(text("PRAGMA quick_check"))
            integrity = [row[0] for row in result.fetchall()]
            if integrity != ["ok"]:
                raise RuntimeError(f"SQLite quick_check: {integrity[:3]}")
            result = await session.execute(text("PRAGMA foreign_key_check"))
            violations = result.fetchmany(3)
            if violations:
                raise RuntimeError(f"SQLite foreign_key_check: {violations}")
        return True

    async def close(self) -> None:
        await self.engine.dispose()
