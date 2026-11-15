"""Доступ к SQLite через SQLAlchemy async и реальные Alembic-миграции."""
from __future__ import annotations

import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import AuditEntry, GuildSetting


def migrate(database_url: str) -> None:
    """Запускается до инициализации async engine; Alembic использует sync sqlite драйвер."""
    if not database_url.startswith("sqlite+aiosqlite:///"):
        raise ValueError("Неподдерживаемый драйвер БД")
    cfg = AlembicConfig(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", database_url.replace("sqlite+aiosqlite", "sqlite", 1))
    command.upgrade(cfg, "head")


class Database:
    def __init__(self, url: str):
        self.engine = create_async_engine(url, pool_pre_ping=True)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.sessions() as session:
            async with session.begin():
                yield session

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

    async def ping(self) -> bool:
        from sqlalchemy import text
        async with self.sessions() as session:
            await session.execute(text("SELECT 1"))
        return True

    async def close(self) -> None:
        await self.engine.dispose()
