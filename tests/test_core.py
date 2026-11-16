"""Тесты без Discord-токена или подключения к серверу."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import GUILD_ID, Config
from app.core.catalog import MODULES, implemented
from app.core.guild_guard import is_allowed_guild, is_allowed_interaction
from app.core.permissions import can_manage_settings
from app.healthcheck import is_healthy


def test_guild_guard():
    assert is_allowed_guild(GUILD_ID)
    assert not is_allowed_guild(1)
    assert not is_allowed_guild(None)
    assert is_allowed_interaction(SimpleNamespace(guild_id=GUILD_ID))
    assert not is_allowed_interaction(SimpleNamespace(guild_id=None))


def test_owner_and_admin_permissions():
    guild = SimpleNamespace(owner_id=10)

    def interaction(user_id, admin, guild_id=GUILD_ID):
        return SimpleNamespace(guild_id=guild_id, guild=guild, user=SimpleNamespace(
            id=user_id, guild_permissions=SimpleNamespace(administrator=admin)))

    assert can_manage_settings(interaction(10, False), frozenset())
    assert can_manage_settings(interaction(20, True), frozenset())
    assert can_manage_settings(interaction(30, False), frozenset({30}))
    assert not can_manage_settings(interaction(40, False), frozenset())
    assert not can_manage_settings(interaction(10, True, 123), frozenset({10}))


def test_catalog_and_implemented_modules():
    assert len(MODULES) == 16
    assert len({m.key for m in MODULES}) == 16
    assert {m.key for m in MODULES if implemented(m.key)} == {"membership", "moderation", "audit_logs", "welcome", "roles", "tickets", "leveling", "profiles", "economy", "achievements", "quests", "voice", "party_finder", "events"}
    assert "custom" not in {m.key for m in MODULES}


def test_config_rejects_other_guild(monkeypatch, tmp_path):
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    monkeypatch.setenv("DISCORD_GUILD_ID", "1")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="NewSquad"):
        Config.from_env()


def test_missing_token(monkeypatch):
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    monkeypatch.setenv("DISCORD_GUILD_ID", str(GUILD_ID))
    with pytest.raises(ValueError, match="DISCORD_TOKEN"):
        Config.from_env()


def test_healthcheck(tmp_path: Path):
    health = tmp_path / "health.json"
    assert not is_healthy(health, now=100)
    health.write_text('{"ready":true,"timestamp":90}', encoding="utf-8")
    assert is_healthy(health, now=100)
    assert not is_healthy(health, now=200)
    health.write_text('{"ready":false,"timestamp":100}', encoding="utf-8")
    assert not is_healthy(health, now=100)
