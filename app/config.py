"""Настройки запуска. Секреты читаются только из окружения."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

GUILD_ID = 1439347293076586638
FEMALE_ROLE_ID = 1453273881359024231
VERSION = "0.5.0"


@dataclass(frozen=True, slots=True)
class Config:
    token: str
    guild_id: int
    database_url: str
    data_dir: Path
    timezone: str
    log_level: str
    owner_ids: frozenset[int]
    message_content_intent: bool = False

    @classmethod
    def from_env(cls) -> Config:
        token = os.getenv("DISCORD_TOKEN", "").strip()
        if not token or token.startswith("<"):
            raise ValueError("DISCORD_TOKEN не задан. Заполните .env перед запуском.")
        guild_id = int(os.getenv("DISCORD_GUILD_ID", str(GUILD_ID)))
        if guild_id != GUILD_ID:
            raise ValueError("NewSqadBot разрешён только на сервере NewSquad.")
        data_dir = Path(os.getenv("DATA_DIR", "/app/data")).expanduser().resolve()
        default_db = f"sqlite+aiosqlite:///{data_dir / 'newsqad.db'}"
        database_url = os.getenv("DATABASE_URL", default_db)
        if not database_url.startswith("sqlite+aiosqlite:///"):
            raise ValueError("В v0.5.0 поддерживается только SQLite с aiosqlite.")
        timezone = os.getenv("TIMEZONE", "Europe/Tallinn")
        try:
            ZoneInfo(timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Неизвестная таймзона: {timezone}") from exc
        level = os.getenv("LOG_LEVEL", "INFO").upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("LOG_LEVEL должен быть DEBUG, INFO, WARNING, ERROR или CRITICAL.")
        raw_owners = os.getenv("BOT_OWNER_IDS", "").strip()
        owners = frozenset(int(i.strip()) for i in raw_owners.split(",") if i.strip())
        if any(i <= 0 for i in owners):
            raise ValueError("BOT_OWNER_IDS содержит некорректный Discord ID.")
        content_intent = os.getenv("ENABLE_MESSAGE_CONTENT_INTENT", "false").strip().lower() in ("1", "true", "yes")
        return cls(token, guild_id, database_url, data_dir, timezone, level, owners, content_intent)
