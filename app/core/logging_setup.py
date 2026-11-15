"""Базовое логирование без утечек секретов."""
import logging
import sys


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        stream=sys.stdout,
        force=True,
    )
    logging.getLogger("discord.http").setLevel(logging.WARNING)
