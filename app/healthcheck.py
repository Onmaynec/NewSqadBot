"""Healthcheck без внешнего HTTP-порта и токена Discord."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path


def is_healthy(path: Path, *, now: float | None = None, max_age: int = 90) -> bool:
    try:
        health = json.loads(path.read_text(encoding="utf-8"))
        if health.get("ready") is not True:
            return False
        # Старые health-файлы совместимы; v1.4+ проверяет фоновое ядро.
        if health.get("scheduler") is False or health.get("event_bus") is False:
            return False
        if health.get("discord") is False or health.get("sqlite") is False:
            return False
        timestamp = float(health["timestamp"])
        current = time.time() if now is None else now
        return 0 <= current - timestamp <= max_age
    except (OSError, ValueError, TypeError, KeyError):
        return False


def main() -> int:
    path = Path(os.getenv("DATA_DIR", "/app/data")) / "health.json"
    return 0 if is_healthy(path) else 1


if __name__ == "__main__":
    raise SystemExit(main())
