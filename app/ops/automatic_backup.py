"""Periodic SQLite online snapshots with off-volume export and restore probes.

Does not modify the active SQLite file. Existing create/verify/restore CLI stays
compatible. Only files with the newsqad-auto- prefix are eligible for rotation.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from app.ops.backup import BackupError, database_path, snapshot, verify

log = logging.getLogger(__name__)
PREFIX = "newsqad-auto-"


@dataclass(frozen=True, slots=True)
class BackupSettings:
    interval_hours: int = 24
    keep: int = 7
    export_dir: Path | None = None

    @classmethod
    def from_env(cls) -> BackupSettings:
        interval_hours = int(os.getenv("BACKUP_INTERVAL_HOURS", "24"))
        keep = int(os.getenv("BACKUP_KEEP", "7"))
        if not 1 <= interval_hours <= 168 or not 1 <= keep <= 60:
            raise ValueError("BACKUP_INTERVAL_HOURS должен быть 1–168, BACKUP_KEEP — 1–60.")
        raw = os.getenv("BACKUP_EXPORT_DIR", "").strip()
        export = Path(raw).expanduser().resolve() if raw else None
        return cls(interval_hours, keep, export)


def rotation(directory: Path, *, keep: int) -> int:
    """Never delete manual backups, restore checkpoints or non-matching names."""
    candidates = sorted(
        (p for p in directory.glob(f"{PREFIX}*.db") if p.is_file()),
        key=lambda p: p.name,
        reverse=True,
    ) if directory.exists() else []
    for file in candidates[keep:]:
        file.unlink()
    return max(len(candidates) - keep, 0)


def restore_probe(source: Path) -> None:
    """Actually copy into a fresh SQLite DB and run integrity/foreign-key checks."""
    with tempfile.TemporaryDirectory(prefix="newsqad-restore-probe-") as temp:
        restored = Path(temp) / "restored.db"
        snapshot(source, restored)
        verify(restored)


def create_automatic_backup(source: Path, settings: BackupSettings) -> tuple[Path, Path | None]:
    source = source.resolve()
    folder = source.parent / "backups"
    name = f"{PREFIX}{time.time_ns():019d}.db"
    local = snapshot(source, folder / name)
    restore_probe(local)
    exported = None
    try:
        if settings.export_dir is not None:
            destination = settings.export_dir.resolve()
            if destination == folder.resolve() or destination.is_relative_to(source.parent):
                raise BackupError("Экспорт должен находиться вне каталога активной SQLite базы.")
            if not destination.is_dir():
                raise BackupError("BACKUP_EXPORT_DIR отсутствует. Создайте каталог и предоставьте права пользователю bot.")
            exported = snapshot(local, destination / name)
            restore_probe(exported)
            rotation(destination, keep=settings.keep)
        return local, exported
    finally:
        # Retain local copies even when off-volume export fails; cap their count.
        rotation(folder, keep=settings.keep)


class BackupWorker:
    def __init__(self, source: Path, settings: BackupSettings):
        self.source = source
        self.settings = settings
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self.last_success: int | None = None
        self.last_export: int | None = None
        self.last_verified: int | None = None
        self.last_error: str | None = None
        self.failures = 0

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def run_once(self) -> bool:
        async with self._lock:
            try:
                local, exported = await asyncio.to_thread(
                    create_automatic_backup, self.source, self.settings)
                now = int(time.time())
                self.last_success = now
                self.last_verified = now
                if exported is not None:
                    self.last_export = now
                self.last_error = None
                log.info("SQLite backup: snapshot=%s off_volume=%s probe=ok", local.name,
                         bool(exported))
                return True
            except Exception as exc:
                self.failures += 1
                # Do not include paths, SQL, or file content in user-visible diagnostics.
                self.last_error = type(exc).__name__
                log.exception("Не удалось выполнить автоматическое резервирование SQLite")
                return False

    def status(self) -> dict:
        return {
            "running": self.running,
            "last_success": self.last_success,
            "last_export": self.last_export,
            "last_verified": self.last_verified,
            "export_enabled": self.settings.export_dir is not None,
            "last_error": self.last_error,
            "failures": self.failures,
        }

    async def _loop(self) -> None:
        while True:
            try:
                await self.run_once()
                await asyncio.sleep(self.settings.interval_hours * 3600)
            except asyncio.CancelledError:
                raise

    def start(self) -> None:
        if not self.running:
            self._task = asyncio.create_task(self._loop(), name="newsqad-backup")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


def main() -> int:
    parser = argparse.ArgumentParser(description="NewSqadBot automatic backup operations")
    parser.add_argument("command", choices=("run-once", "probe"))
    parser.add_argument("file", nargs="?", type=Path)
    args = parser.parse_args()
    if args.command == "probe":
        if args.file is None:
            parser.error("probe requires a snapshot path")
        restore_probe(args.file)
        print("Восстановление во временную БД и проверка целостности прошли успешно.")
    else:
        _, export = create_automatic_backup(database_path(), BackupSettings.from_env())
        print("Автоматический снимок проверен. Экспорт за пределы volume:", bool(export))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
