"""Запуск NewSqadBot с корректным завершением при SIGTERM/SIGINT."""
from __future__ import annotations

import asyncio
import logging
import signal

from dotenv import load_dotenv

from app.bot import NewSqadBot
from app.config import Config
from app.core.logging_setup import setup_logging

log = logging.getLogger(__name__)


async def serve(config: Config) -> None:
    bot = NewSqadBot(config)
    loop = asyncio.get_running_loop()
    shutting_down = False

    def request_shutdown(signame: str) -> None:
        nonlocal shutting_down
        if shutting_down:
            return
        shutting_down = True
        log.info("Остановка NewSqadBot: сигнал %s", signame)
        asyncio.create_task(bot.close(), name="newsqad-shutdown")

    for signame in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(signame, request_shutdown, signame.name)
        except (NotImplementedError, RuntimeError):
            # Windows: CTRL+C обрабатывается asyncio.run.
            pass
    async with bot:
        await bot.start(config.token)


def main() -> None:
    load_dotenv()
    config = Config.from_env()
    setup_logging(config.log_level)
    log.info("Запуск NewSqadBot")
    try:
        asyncio.run(serve(config))
    except KeyboardInterrupt:
        log.info("Остановка NewSqadBot с клавиатуры")
    except Exception:
        log.exception("Критическая ошибка запуска NewSqadBot")
        raise
    finally:
        log.info("Завершение процесса NewSqadBot")


if __name__ == "__main__":
    main()
