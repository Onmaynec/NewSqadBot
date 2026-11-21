"""v1.0.0 — приёмка ядра, SQLite, приватности, Docker и сквозного сценария."""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from app.config import GUILD_ID, VERSION
from app.core.database import Database, migrate
from app.core.logging_setup import DockerFormatter, SecretFilter
from app.ops.backup import BackupError, restore, snapshot, verify
from app.ui.theme import BRAND_BLUE, LoggedModal, LoggedView, card, notice


def test_version_theme_and_action_statuses():
    assert VERSION == "1.9.0"
    assert BRAND_BLUE == 0x4A86F7
    assert card("Информация").color == discord.Color(BRAND_BLUE)
    for kind in ("info", "success", "warning", "error"):
        embed = notice("@everyone проверка", kind=kind)
        assert embed.color == discord.Color(BRAND_BLUE)
        assert "@everyone" not in embed.description
        assert len(embed.title) < 256


@pytest.mark.asyncio
async def test_view_and_modal_reject_foreign_guild():
    view = LoggedView(timeout=60)
    modal = LoggedModal(title="Проверка")
    response = SimpleNamespace(
        is_done=MagicMock(return_value=False),
        send_message=AsyncMock(),
    )
    interaction = SimpleNamespace(
        guild_id=42, response=response,
        followup=SimpleNamespace(send=AsyncMock()),
    )
    assert await view.interaction_check(interaction) is False
    assert await modal.interaction_check(interaction) is False
    assert response.send_message.await_count == 2
    assert response.send_message.await_args.kwargs["ephemeral"] is True
    interaction.guild_id = GUILD_ID
    assert await view.interaction_check(interaction) is True
    assert await modal.interaction_check(interaction) is True


def test_docker_log_redacts_discord_tokens_and_webhooks(monkeypatch):
    token = "abc.SuperSecretToken.123"
    monkeypatch.setenv("DISCORD_TOKEN", token)
    raw = (
        f"Discord returned {token}; Authorization: Bot {token}; "
        "https://discord.com/api/webhooks/12345678/webhook-secret-123 "
        "service=Membership"
    )
    record = logging.LogRecord("app.test", logging.ERROR, "module.py", 41, raw, (), None)
    formatter = DockerFormatter("%(name)s:%(lineno)d %(message)s")
    formatted = formatter.format(record)
    assert "SuperSecret" not in formatted
    assert "webhook-secret-123" not in formatted
    assert "[REDACTED]" in formatted
    assert "app.test:41" in formatted
    another = logging.LogRecord("app", logging.ERROR, "file", 1, raw, (), None)
    assert SecretFilter().filter(another)
    assert "SuperSecret" not in another.getMessage()
    assert "webhook-secret-123" not in another.getMessage()


def test_backup_full_cycle_and_running_guard(tmp_path):
    source = tmp_path / "data" / "newsqad.db"
    source.parent.mkdir()
    migrate(f"sqlite+aiosqlite:///{source}")
    with sqlite3.connect(source) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "INSERT INTO guild_settings(guild_id,key,value_json) VALUES(?,?,?)",
            (GUILD_ID, "release_test", json.dumps("saved")),
        )
    first = tmp_path / "copies" / "first.db"
    snapshot(source, first)
    verify(first)
    with pytest.raises(BackupError):
        snapshot(source, first)
    with sqlite3.connect(source) as conn:
        conn.execute(
            "UPDATE guild_settings SET value_json=? WHERE key='release_test'",
            (json.dumps("changed"),),
        )
    state = source.parent / "health.json"
    now = time.time()
    state.write_text(json.dumps({"ready": True, "timestamp": now}), encoding="utf-8")
    with pytest.raises(BackupError, match="Бот ещё работает"):
        restore(first, source, now=now + 2)
    state.write_text(json.dumps({"ready": False, "timestamp": now}), encoding="utf-8")
    # Раньше ready=False разрешал restore при ещё открытой SQLite.
    with pytest.raises(BackupError, match="Бот ещё работает"):
        restore(first, source, now=now + 5)
    state.write_text(json.dumps({"ready": False, "phase": "stopped", "timestamp": now}),
                     encoding="utf-8")
    restore(first, source, now=now + 5)
    with sqlite3.connect(source) as conn:
        assert json.loads(conn.execute(
            "SELECT value_json FROM guild_settings WHERE key='release_test'"
        ).fetchone()[0]) == "saved"
        assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    assert list((source.parent / "backups").glob("before-restore-*.db"))
    with pytest.raises(BackupError):
        verify(tmp_path / "missing.db")


@pytest.mark.asyncio
async def test_e2e_membership_party_events_suggestions_statistics_restart(tmp_path):
    from app.services import applications, events, party_finder, statistics, suggestions

    dbfile = tmp_path / "integrated.db"
    url = f"sqlite+aiosqlite:///{dbfile}"
    migrate(url)
    db = Database(url)
    now = int(time.time())
    try:
        applicant = await applications.submit(
            db, guild_id=GUILD_ID, user_id=900,
            username="example", name="Лиза", gender="female", age=20,
            inviter="Друг",
        )
        assert await applications.transition(
            db, applicant.id, expected=("pending",), target="approving",
            actor_id=42, reason="Одобрено", decision="approve",
        )
        assert await applications.transition(
            db, applicant.id, expected=("approving",), target="approved",
            actor_id=42, reason="Доступ выдан",
        )
        party = await party_finder.create(
            db, guild_id=GUILD_ID, owner_id=900, game="Minecraft",
            description="Вместе исследуем мир", capacity=2,
            expires_at=now + 7200,
        )
        party = await party_finder.join(db, GUILD_ID, party.id, 901)
        assert party.status == "full"
        event = await events.create_event(
            db, guild_id=GUILD_ID, creator_id=900, host_id=900,
            channel_id=100, title="Совместная игра",
            description="Собираемся играть в субботу",
            timezone_name="UTC", starts_at=now + 3600,
            now=now,
        )
        assert await events.register(
            db, GUILD_ID, event.id, 900, now=now + 1,
        ) == "registered"
        suggestion = await suggestions.create(
            db, guild_id=GUILD_ID, author_id=900, channel_id=100,
            topic="Новые турниры",
            description="Проводить больше общих турниров.",
            now=now,
        )
        changed, suggestion = await suggestions.vote(
            db, guild_id=GUILD_ID, suggestion_id=suggestion.id,
            user_id=901, choice=1,
        )
        assert changed and suggestion.votes_for == 1
        assert await statistics.record_activity(
            db, guild_id=GUILD_ID, user_id=900, kind="message",
            reference=999999, timestamp=now, timezone="UTC",
        )
        assert not await statistics.record_activity(
            db, guild_id=GUILD_ID, user_id=900, kind="message",
            reference=999999, timestamp=now, timezone="UTC",
        )
        ids = (applicant.id, party.id, event.id, suggestion.id)
    finally:
        await db.close()

    backup_file = tmp_path / "copies" / "integrated.db"
    snapshot(dbfile, backup_file)
    verify(backup_file)
    migrate(url)
    db = Database(url)
    try:
        from app.services import applications, events, party_finder, statistics, suggestions

        applicant = await applications.get_application(db, ids[0])
        party = await party_finder.get(db, GUILD_ID, ids[1])
        event = await events.get_event(db, GUILD_ID, ids[2])
        suggestion = await suggestions.get(db, GUILD_ID, ids[3])
        assert applicant.status == "approved"
        assert party.status == "full" and party.member_count == 2
        assert event.confirmed_count == 1
        assert suggestion.votes_for == 1
        bounds = statistics.period_bounds("7d", timezone="UTC", now=now)
        series = await statistics.activity_series(db, GUILD_ID, bounds)
        assert sum(row[1] for row in series) == 1
    finally:
        await db.close()


def test_docker_supply_chain_and_smoke_files():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    compose = (root / "compose.yaml").read_text(encoding="utf-8")
    workflow = (root / ".github" / "workflows" / "checks.yml").read_text(encoding="utf-8")
    assert "ARG PYTHON_BASE_IMAGE=" in dockerfile
    assert "public.ecr.aws/docker/library/python:3.12-slim-bookworm" in dockerfile
    assert "no-new-privileges:true" in compose
    assert "read_only: true" in compose
    assert "app.ops.smoke" in workflow
    assert "docker build" in workflow
