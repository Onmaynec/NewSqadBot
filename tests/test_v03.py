"""Regression tests for migration 0003, role guards and schema integrity."""
from __future__ import annotations

import sqlite3
from types import SimpleNamespace as NS

import pytest

from app.config import FEMALE_ROLE_ID, GUILD_ID
from app.core.database import migrate
from app.services.role_rules import (
    forbidden_role_ids,
    is_approved,
    role_safe_for_self_assign,
    validate_panel,
)


class Role:
    def __init__(self, role_id=42, **permissions):
        defaults = dict(administrator=False, manage_guild=False, manage_roles=False,
            ban_members=False, kick_members=False, moderate_members=False, manage_messages=False,
            manage_channels=False, manage_webhooks=False, mention_everyone=False,
            manage_nicknames=False, manage_threads=False, view_audit_log=False)
        defaults.update(permissions)
        self.id = role_id
        self.position = 5
        self.permissions = NS(**defaults)
        self.managed = False

    def is_default(self):
        return False

    def __le__(self, other):
        return self.position <= other.position


class Position:
    def __init__(self, position: int):
        self.position = position

    def __le__(self, other):
        return self.position <= other.position


def test_member_guard_rejects_candidates_and_screening():
    approved = NS(roles=[NS(id=90)], pending=False)
    assert is_approved(approved, 90)
    assert not is_approved(approved, None)
    assert not is_approved(approved, 91)
    assert not is_approved(NS(roles=[NS(id=90)], pending=True), 90)


def test_role_safety_for_self_assign_and_protection():
    role = Role()
    bot_member = NS(guild_permissions=NS(manage_roles=True), top_role=Position(10))
    assert role_safe_for_self_assign(role, bot_member, set())
    assert not role_safe_for_self_assign(role, bot_member, {42})
    role.permissions.administrator = True
    assert not role_safe_for_self_assign(role, bot_member, set())
    role.permissions.administrator = False
    role.position = 12
    assert not role_safe_for_self_assign(role, bot_member, set())
    assert FEMALE_ROLE_ID in forbidden_role_ids({"member_role_id": 17})
    assert 17 in forbidden_role_ids({"member_role_id": 17})


def test_panel_count_limits():
    for mode in ("single", "multi"):
        validate_panel(mode, 1)
        validate_panel(mode, 25)
        for count in (0, 26):
            with pytest.raises(ValueError):
                validate_panel(mode, count)
    with pytest.raises(ValueError):
        validate_panel("invalid", 1)


def test_roles_migration_and_unique_constraints(tmp_path):
    path = tmp_path / "test-v03.db"
    url = f"sqlite+aiosqlite:///{path}"
    migrate(url)
    migrate(url)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0011_suggestions"
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("INSERT INTO role_panels (guild_id,channel_id,title,description,mode,enabled,created_by,created_at) "
                     "VALUES (?,?,?,?,?,?,?,?)", (GUILD_ID, 1234, "Игры", "Выберите игру", "multi", 1, 1, 1))
        panel_id = conn.execute("SELECT id FROM role_panels").fetchone()[0]
        conn.execute("INSERT INTO role_panel_options (panel_id,role_id,label) VALUES (?,?,?)", (panel_id, 88, "Deadlock"))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO role_panel_options (panel_id,role_id,label) VALUES (?,?,?)", (panel_id, 88, "Дубликат"))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO role_panels (guild_id,channel_id,title,description,mode,enabled,created_by,created_at) "
                         "VALUES (?,?,?,?,?,?,?,?)", (GUILD_ID, 1234, "Не работает", "", "bad", 1, 1, 1))
        conn.execute("INSERT INTO welcome_deliveries (guild_id,application_id,user_id,status,attempted_at) "
                     "VALUES (?,?,?,?,?)", (GUILD_ID, 1, 5, "sent", 123))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO welcome_deliveries (guild_id,application_id,user_id,status,attempted_at) "
                         "VALUES (?,?,?,?,?)", (GUILD_ID, 1, 5, "pending", 124))
        conn.execute("INSERT INTO level_role_rewards (guild_id,level,role_id) VALUES (?,?,?)", (GUILD_ID, 10, 77))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO level_role_rewards (guild_id,level,role_id) VALUES (?,?,?)", (GUILD_ID, 10, 77))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO level_role_rewards (guild_id,level,role_id) VALUES (?,?,?)", (GUILD_ID, 0, 88))
        conn.execute("INSERT INTO temporary_roles (guild_id,user_id,role_id,granted_by,granted_at,expires_at,status) "
                     "VALUES (?,?,?,?,?,?,?)", (GUILD_ID, 9, 88, 3, 1, 20, "active"))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO temporary_roles (guild_id,user_id,role_id,granted_by,granted_at,expires_at,status) "
                         "VALUES (?,?,?,?,?,?,?)", (GUILD_ID, 9, 88, 3, 1, 30, "granting"))
        conn.execute("UPDATE temporary_roles SET status='expired' WHERE user_id=9")
        conn.execute("INSERT INTO temporary_roles (guild_id,user_id,role_id,granted_by,granted_at,expires_at,status) "
                     "VALUES (?,?,?,?,?,?,?)", (GUILD_ID, 9, 88, 3, 1, 30, "active"))


def test_welcome_render_and_ui_imports():
    pytest.importorskip("discord")
    from app.modules.welcome import render_template, validate_template
    assert validate_template("Привет, {user}! {server} · {member_count}")
    with pytest.raises(ValueError):
        validate_template("{nickname}")
    with pytest.raises(ValueError):
        validate_template(" ")
    member = NS(mention="<@123>", name="abc", guild=NS(name="NewSquad", member_count=10))
    assert render_template("Привет {user}, {member_count} в {server}", member=member) == "Привет <@123>, 10 в NewSquad"
