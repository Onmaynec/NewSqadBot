"""Настройки запуска. Секреты читаются только из окружения."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

GUILD_ID = 1439347293076586638
FEMALE_ROLE_ID = 1453273881359024231
VERSION = "1.8.1"


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
    web_admin_password_hash: str = ""
    web_admin_port: int = 8787
    web_admin_bind: str = "127.0.0.1"
    web_discord_client_id: str = ""
    web_discord_client_secret: str = ""
    web_public_url: str = ""
    presence_intent: bool = False

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
            raise ValueError("Поддерживается только SQLite с aiosqlite.")
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
        admin_hash = os.getenv("WEB_ADMIN_PASSWORD_HASH", "").strip()
        if admin_hash:
            from app.web.auth import validate_hash

            if not validate_hash(admin_hash):
                raise ValueError("Неверный WEB_ADMIN_PASSWORD_HASH. Используйте python -m app.web.auth hash.")
        try:
            admin_port = int(os.getenv("WEB_ADMIN_PORT", "8787"))
        except ValueError as exc:
            raise ValueError("WEB_ADMIN_PORT должен быть целым числом.") from exc
        admin_bind = os.getenv("WEB_ADMIN_BIND", "127.0.0.1").strip()
        if not 1 <= admin_port <= 65535 or admin_bind not in ("127.0.0.1", "0.0.0.0"):
            raise ValueError("Неверный адрес или порт локальной панели.")
        client_id = os.getenv("WEB_DISCORD_CLIENT_ID", "").strip()
        client_secret = os.getenv("WEB_DISCORD_CLIENT_SECRET", "").strip()
        public_url = os.getenv("WEB_PUBLIC_URL", "").strip().rstrip("/")
        oauth_values = (client_id, client_secret, public_url)
        if any(oauth_values):
            if not all(oauth_values):
                raise ValueError("Для Discord OAuth нужны WEB_DISCORD_CLIENT_ID, WEB_DISCORD_CLIENT_SECRET и WEB_PUBLIC_URL.")
            parsed = urlsplit(public_url)
            if (not client_id.isdecimal() or parsed.scheme != "https" or not parsed.hostname
                    or parsed.username or parsed.password or parsed.query or parsed.fragment
                    or parsed.path or parsed.port not in (None, 443)):
                raise ValueError("WEB_PUBLIC_URL должен быть HTTPS-адресом без пути, параметров и логина.")
            if len(client_secret) < 12:
                raise ValueError("Discord OAuth client secret выглядит некорректным.")
        return cls(token, guild_id, database_url, data_dir, timezone, level, owners,
                   content_intent, admin_hash, admin_port, admin_bind,
                   client_id, client_secret, public_url,
                   os.getenv("ENABLE_PRESENCE_INTENT", "false").lower() in ("true", "1", "yes"))
