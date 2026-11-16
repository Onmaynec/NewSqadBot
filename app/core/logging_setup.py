"""Контекстные Docker-логи с источником ошибки и защитой токена Discord."""
from __future__ import annotations

import datetime as dt
import logging
import os
import re
import sys
import threading


class DockerFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        formatted = super().format(record)
        token = os.getenv("DISCORD_TOKEN", "").strip()
        if token:
            formatted = formatted.replace(token, "[REDACTED]")
        return re.sub(r"(?i)(authorization\s*[:=]\s*bot\s+)\S+", r"\1[REDACTED]", formatted)

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        timestamp = dt.datetime.fromtimestamp(record.created, dt.timezone.utc)
        return timestamp.isoformat(timespec="milliseconds").replace("+00:00", "Z")


class SecretFilter(logging.Filter):
    def __init__(self) -> None:
        super().__init__()
        self.token = os.getenv("DISCORD_TOKEN", "").strip()

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            raw = record.getMessage()
        except Exception:
            raw = "<ошибка форматирования строки лога>"
        if self.token:
            raw = raw.replace(self.token, "[REDACTED]")
        raw = re.sub(r"(?i)(authorization\s*[:=]\s*bot\s+)\S+", r"\1[REDACTED]", raw)
        record.msg = raw
        record.args = ()
        return True


def setup_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(DockerFormatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d | %(message)s"
    ))
    handler.addFilter(SecretFilter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level))
    logging.getLogger("discord.http").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.captureWarnings(True)

    def uncaught(exc_type, exc_value, tb):
        logging.getLogger("uncaught").critical("Необработанное исключение в главном потоке",
                                                exc_info=(exc_type, exc_value, tb))

    sys.excepthook = uncaught

    def thread_error(args: threading.ExceptHookArgs) -> None:
        logging.getLogger("uncaught").critical("Необработанное исключение потока %s",
            getattr(args.thread, "name", "?"),
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    threading.excepthook = thread_error
