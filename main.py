"""Точка входа. Функциональная логика находится в app/."""
import logging

from dotenv import load_dotenv

from app.bot import NewSqadBot
from app.config import Config
from app.core.logging_setup import setup_logging


def main() -> None:
    load_dotenv()
    config = Config.from_env()
    setup_logging(config.log_level)
    logging.getLogger(__name__).info("Запуск NewSqadBot")
    bot = NewSqadBot(config)
    bot.run(config.token, log_handler=None)


if __name__ == "__main__":
    main()
