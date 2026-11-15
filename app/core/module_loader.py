"""Независимая загрузка реализованных модулей, без пустых заглушек."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.core.catalog import MODULES, implemented

if TYPE_CHECKING:
    from app.bot import NewSqadBot

logger = logging.getLogger(__name__)


async def load_modules(bot: NewSqadBot) -> dict[str, str]:
    results: dict[str, str] = {}
    for module in MODULES:
        if not implemented(module.key):
            results[module.key] = "planned"
            continue
        enabled = await bot.database.get_setting(bot.config.guild_id, f"module.{module.key}.enabled", True)
        if not enabled:
            results[module.key] = "disabled"
            continue
        try:
            await bot.load_extension(f"app.modules.{module.key}")
        except Exception:
            logger.exception("Не удалось загрузить модуль %s", module.key)
            results[module.key] = "error"
        else:
            results[module.key] = "active"
            logger.info("Модуль %s загружен", module.key)
    return results
