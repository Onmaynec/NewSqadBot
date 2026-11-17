"""Проверка новой схемы, сроков и целостности заявок без Discord API."""
import sqlite3
from pathlib import Path

import pytest

from app.config import FEMALE_ROLE_ID, GUILD_ID, VERSION
from app.core.database import migrate
from app.core.durations import nickname, parse_duration


def test_duration_examples_and_rejections():
    assert parse_duration("30m") == 1800
    assert parse_duration("2h") == 7200
    assert parse_duration("10d") == 864000
    assert parse_duration("2w") == 1209600
    assert parse_duration("per") is None
    for bad in ("permanent", "0m", "-1h", "12x", "1.5h", "10D", "2hours", ""):
        with pytest.raises(ValueError):
            parse_duration(bad)
    with pytest.raises(ValueError):
        parse_duration("per", allow_permanent=False)
    with pytest.raises(ValueError):
        parse_duration("29d", max_seconds=28*86400)


def test_nickname_preserves_name():
    assert nickname("user", "Катя") == "user | Катя"
    long_user = "a" * 32
    assert nickname(long_user, "Александр").endswith(" | Александр")
    assert len(nickname(long_user, "Александр")) == 32
    with pytest.raises(ValueError):
        nickname("user", "@everyone")


def test_contract_constants():
    assert VERSION == "1.5.0"
    assert GUILD_ID == 1439347293076586638
    assert FEMALE_ROLE_ID == 1453273881359024231


def test_migration_constraints(tmp_path: Path):
    db_path = tmp_path / "newsquad.sqlite3"
    migrate(f"sqlite+aiosqlite:///{db_path}")
    with sqlite3.connect(db_path) as c:
        c.execute("PRAGMA foreign_keys=ON")
        now = 123
        fields = (GUILD_ID, 13, "user", "Катя", "female", 21, "Алекс", "pending", now)
        c.execute("INSERT INTO membership_applications (guild_id,user_id,username,name,gender,age,inviter,status,created_at) "
                  "VALUES (?,?,?,?,?,?,?,?,?)", fields)
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("INSERT INTO membership_applications (guild_id,user_id,username,name,gender,age,inviter,status,created_at) "
                      "VALUES (?,?,?,?,?,?,?,?,?)", fields)
        c.execute("UPDATE membership_applications SET status='rejected' WHERE user_id=13")
        c.execute("INSERT INTO membership_applications (guild_id,user_id,username,name,gender,age,inviter,status,created_at) "
                  "VALUES (?,?,?,?,?,?,?,?,?)", fields)
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("INSERT INTO membership_applications (guild_id,user_id,username,name,gender,age,inviter,status,created_at) "
                      "VALUES (?,?,?,?,?,?,?,?,?)", (GUILD_ID, 14, "user", "Саша", "male", 999, "Нет", "pending", now))
        c.execute("INSERT INTO moderation_cases (guild_id,user_id,moderator_id,kind,reason,status,created_at) "
                  "VALUES (?,?,?,?,?,?,?)", (GUILD_ID, 14, 55, "ban", "Причина", "active", now))
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("INSERT INTO moderation_cases (guild_id,user_id,moderator_id,kind,reason,status,created_at) "
                      "VALUES (?,?,?,?,?,?,?)", (GUILD_ID, 14, 55, "ban", "Причина", "active", now))
