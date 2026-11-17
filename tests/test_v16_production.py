"""Production 1.6: восстановление БД, отказные состояния и защита секретов."""
from __future__ import annotations

import json
import sqlite3

import pytest
from sqlalchemy import text

from app.config import GUILD_ID, Config
from app.core.database import Database, migrate
from app.core.logging_setup import _redact
from app.ops.backup import BackupError, restore, snapshot, verify
from app.web.auth import hash_password


def test_degraded_or_partial_process_blocks_live_database_restore(tmp_path):
    path = tmp_path / "real.db"
    backup = tmp_path / "backup.db"
    migrate(f"sqlite+aiosqlite:///{path}")
    snapshot(path, backup)
    health = path.parent / "health.json"
    health.write_text(json.dumps({
        "ready": False, "phase": "degraded", "timestamp": 100,
    }), encoding="utf-8")
    with pytest.raises(BackupError, match="остановите контейнер"):
        restore(backup, path, now=101)
    # Старый healthfile также может показывать ready=false при живом процессе.
    health.write_text(json.dumps({"ready": False, "timestamp": 100}), encoding="utf-8")
    with pytest.raises(BackupError):
        restore(backup, path, now=101)
    health.write_text(json.dumps({
        "ready": False, "phase": "stopped", "timestamp": 100,
    }), encoding="utf-8")
    restore(backup, path, now=101)
    verify(path)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA quick_check").fetchone()[0] == "ok"


@pytest.mark.asyncio
async def test_every_sqlite_connection_enforces_foreign_keys_and_timeout(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'safe.db'}"
    migrate(url)
    db = Database(url)
    try:
        async with db.sessions() as session:
            assert await session.scalar(text("PRAGMA foreign_keys")) == 1
            assert await session.scalar(text("PRAGMA busy_timeout")) == 15_000
        assert await db.check_integrity()
    finally:
        await db.close()


def test_log_redaction_covers_bot_token_and_admin_hash(monkeypatch):
    token = "FAKE_DISCORD_SECRET_TOKEN"
    password_hash = "pbkdf2_sha256:600000:0123456789abcdef:abcdef"
    monkeypatch.setenv("WEB_ADMIN_PASSWORD_HASH", password_hash)
    monkeypatch.setenv("DISCORD_TOKEN", token)
    data = _redact(f"Auth {token}, web_hash={password_hash}, "
                   "Authorization: Bot abc123", token=token)
    assert token not in data
    assert password_hash not in data
    assert "Bot abc123" not in data
    assert "[REDACTED]" in data


def test_invalid_web_admin_credentials_fail_closed(monkeypatch, tmp_path):
    monkeypatch.setenv("DISCORD_TOKEN", "sample-token")
    monkeypatch.setenv("DISCORD_GUILD_ID", str(GUILD_ID))
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("WEB_ADMIN_PASSWORD_HASH", "plaintext-password")
    with pytest.raises(ValueError, match="WEB_ADMIN_PASSWORD_HASH"):
        Config.from_env()
    valid = hash_password("a long enough local password")
    monkeypatch.setenv("WEB_ADMIN_PASSWORD_HASH", valid)
    monkeypatch.setenv("WEB_ADMIN_BIND", "127.0.0.1")
    monkeypatch.setenv("WEB_ADMIN_PORT", "8787")
    config = Config.from_env()
    assert config.web_admin_password_hash == valid
    assert config.web_admin_port == 8787
    monkeypatch.setenv("WEB_ADMIN_BIND", "10.0.0.5")
    with pytest.raises(ValueError, match="адрес или порт"):
        Config.from_env()


def test_static_web_assets_present_and_no_inline_script():
    from pathlib import Path

    directory = Path(__file__).resolve().parents[1] / "app/web/static"
    html = (directory / "index.html").read_text(encoding="utf-8")
    css = (directory / "style.css").read_text(encoding="utf-8")
    script = (directory / "app.js").read_text(encoding="utf-8")
    assert '<script src="/static/app.js" defer></script>' in html
    assert '<link rel="stylesheet" href="/static/style.css">' in html
    assert "<script>" not in html
    assert "login-screen" in html and "settings-grid" in html
    assert "--canvas" in css and "renderSettings" in script
    assert "DISCORD_TOKEN" not in script
