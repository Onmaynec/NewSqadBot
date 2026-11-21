"""Small, privacy-safe operational journal retained across process restarts.

Only fixed event types, timestamps and aggregate HTTP status counts are stored.
No Discord message content, user identifiers, URLs, credentials or exception text.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from collections import deque
from pathlib import Path

log = logging.getLogger(__name__)
EVENTS = frozenset({
    "gateway.connected", "gateway.resumed", "gateway.disconnected",
    "worker.scheduler_restarted", "worker.event_bus_restarted",
    "module.load_failed", "discord.http_error",
})
MAX_HISTORY = 40


class OperationalJournal:
    def __init__(self, path: Path):
        self.path = path
        self.history: deque[dict[str, int | str]] = deque(maxlen=MAX_HISTORY)
        self.last_connected_at: int | None = None
        self.last_disconnected_at: int | None = None
        self.http_errors = 0
        self.http_rate_limits = 0
        self.last_http_status: int | None = None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for item in data.get("history", [])[-MAX_HISTORY:]:
                if item.get("kind") in EVENTS and type(item.get("at")) is int:
                    record = {"kind": item["kind"], "at": item["at"]}
                    module = item.get("module")
                    if item["kind"] == "module.load_failed" and isinstance(module, str) and re.fullmatch(r"[a-z0-9_]{1,60}", module):
                        record["module"] = module
                    self.history.append(record)
            for name in ("last_connected_at", "last_disconnected_at", "last_http_status"):
                value = data.get(name)
                if type(value) is int and value >= 0:
                    setattr(self, name, value)
            for name in ("http_errors", "http_rate_limits"):
                value = data.get(name)
                if type(value) is int and value >= 0:
                    setattr(self, name, value)
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def record(self, kind: str, *, now: int | None = None, module: str | None = None) -> None:
        if kind not in EVENTS:
            raise ValueError("Unknown operational event")
        timestamp = int(time.time()) if now is None else now
        item: dict[str, int | str] = {"kind": kind, "at": timestamp}
        if kind == "module.load_failed" and module is not None:
            if not re.fullmatch(r"[a-z0-9_]{1,60}", module):
                raise ValueError("Invalid module identifier")
            item["module"] = module
        self.history.append(item)
        if kind in ("gateway.connected", "gateway.resumed"):
            self.last_connected_at = timestamp
        if kind == "gateway.disconnected":
            self.last_disconnected_at = timestamp
        self._persist()

    def http_failure(self, status: int) -> None:
        self.http_errors += 1
        if status == 429:
            self.http_rate_limits += 1
        self.last_http_status = status
        # Bound disk writes during outages; counters remain current in memory.
        if self.http_errors == 1 or self.http_errors % 10 == 0:
            self.record("discord.http_error")

    def snapshot(self) -> dict:
        return {
            "last_connected_at": self.last_connected_at,
            "last_disconnected_at": self.last_disconnected_at,
            "http_errors": self.http_errors,
            "http_rate_limits": self.http_rate_limits,
            "last_http_status": self.last_http_status,
            "history": list(self.history),
        }

    def _persist(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_name(self.path.name + ".tmp")
            with temp.open("w", encoding="utf-8") as out:
                json.dump(self.snapshot(), out, ensure_ascii=False, separators=(",", ":"))
            os.replace(temp, self.path)
        except OSError:
            # Observability must not bring the Discord bot down.
            log.warning("Не удалось сохранить журнал состояния (проверьте права на DATA_DIR)")
