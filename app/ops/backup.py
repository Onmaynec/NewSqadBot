"""Безопасные резервные копии и восстановление SQLite.

Используется SQLite Online Backup API; обычное копирование .db из WAL не годится.
Никогда не запускать restore параллельно с работающим процессом бота.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import tempfile
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.engine import make_url


class BackupError(RuntimeError):
    pass


def database_path() -> Path:
    url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:////app/data/newsqad.db")
    parsed = make_url(url)
    if parsed.drivername not in ("sqlite+aiosqlite", "sqlite") or parsed.database is None:
        raise BackupError("Backup поддерживает только SQLite.")
    return Path(parsed.database).resolve()


def verify(path: Path) -> None:
    if not path.is_file():
        raise BackupError(f"Файл БД не найден: {path}")
    connection = None
    try:
        connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        integrity = connection.execute("PRAGMA quick_check").fetchone()
        if integrity is None or integrity[0] != "ok":
            raise BackupError(f"Ошибка целостности SQLite: {integrity!r}")
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_keys:
            raise BackupError(f"Нарушены внешние ключи: {len(foreign_keys)}")
        version = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='alembic_version'"
        ).fetchone()
        if version is None:
            raise BackupError("Это не БД NewSqadBot: отсутствует alembic_version.")
    except sqlite3.DatabaseError as exc:
        raise BackupError("Не удалось проверить SQLite-файл.") from exc
    finally:
        if connection:
            connection.close()


def snapshot(source: Path, target: Path) -> Path:
    source, target = source.resolve(), target.resolve()
    if source == target or target.exists():
        raise BackupError("Целевой файл совпадает с исходным или уже существует.")
    if not source.is_file():
        raise BackupError("Исходная база данных не найдена.")
    target.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(target.parent, 0o700)
    descriptor, temp_name = tempfile.mkstemp(prefix=".newsqad-", suffix=".db", dir=target.parent)
    os.close(descriptor)
    temp = Path(temp_name)
    try:
        os.chmod(temp, 0o600)
        with closing(sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True)) as src:
            with closing(sqlite3.connect(temp)) as dst:
                src.backup(dst)
                dst.commit()
        verify(temp)
        os.replace(temp, target)
        return target
    finally:
        temp.unlink(missing_ok=True)


def _guard_running(target: Path, *, now: float | None = None) -> None:
    path = target.parent / "health.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        elapsed = (time.time() if now is None else now) - float(state["timestamp"])
        if state.get("ready") is True and 0 <= elapsed <= 90:
            raise BackupError("Бот ещё работает: остановите контейнер перед восстановлением.")
    except (OSError, ValueError, TypeError, KeyError):
        return


def restore(source: Path, target: Path, *, now: float | None = None) -> Path:
    source, target = source.resolve(), target.resolve()
    if source == target:
        raise BackupError("Нельзя восстанавливать БД поверх того же файла.")
    _guard_running(target, now=now)
    verify(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Сохраняем текущую базу отдельно, если восстановление будет ошибочным.
    if target.exists():
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        previous = target.parent / "backups" / f"before-restore-{timestamp}.db"
        if previous.exists():
            raise BackupError("Резервная копия перед восстановлением уже существует.")
        snapshot(target, previous)
        # Закрываем WAL до замены файла во избежание применения старых страниц.
        with closing(sqlite3.connect(target)) as live:
            checkpoint = live.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if checkpoint and checkpoint[0]:
                raise BackupError("WAL занят. Остановите все процессы, использующие SQLite.")
    fd, temporary = tempfile.mkstemp(prefix=".newsqad-restore-", suffix=".db", dir=target.parent)
    os.close(fd)
    tmp = Path(temporary)
    try:
        os.chmod(tmp, 0o600)
        with closing(sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True)) as src:
            with closing(sqlite3.connect(tmp)) as dst:
                src.backup(dst)
                dst.commit()
        verify(tmp)
        for suffix in ("-wal", "-shm"):
            Path(str(target) + suffix).unlink(missing_ok=True)
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="Резервирование и восстановление SQLite NewSqadBot")
    cmd = parser.add_subparsers(dest="command", required=True)
    cmd.add_parser("create")
    verify_parser = cmd.add_parser("verify")
    verify_parser.add_argument("file", type=Path)
    restore_parser = cmd.add_parser("restore")
    restore_parser.add_argument("file", type=Path)
    args = parser.parse_args()
    db = database_path()
    if args.command == "create":
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = snapshot(db, db.parent / "backups" / f"newsqad-{timestamp}.db")
        print(f"Создана и проверена резервная копия: {path}")
    elif args.command == "verify":
        verify(args.file)
        print("Резервная копия SQLite прошла проверку целостности.")
    elif args.command == "restore":
        path = restore(args.file, db)
        print(f"База восстановлена: {path}. Перед запуском проверьте актуальность миграций.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
