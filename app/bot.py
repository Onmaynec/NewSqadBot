"""Жизненный цикл NewSqadBot и только серверная регистрация команд."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

import discord
from discord.ext import commands

from app.config import VERSION, Config
from app.core.database import Database, migrate
from app.core.errors import GuildCommandTree
from app.core.event_bus import EventBus
from app.core.module_loader import load_modules
from app.core.scheduler import Scheduler

logger = logging.getLogger(__name__)


class NewSqadBot(commands.Bot):
    def __init__(self, config: Config):
        intents = discord.Intents.none()
        intents.guilds = True
        intents.members = True
        super().__init__(command_prefix=commands.when_mentioned, intents=intents, tree_cls=GuildCommandTree)
        self.config = config
        self.database: Database | None = None
        self.scheduler: Scheduler | None = None
        self.events: EventBus | None = None
        self.module_states: dict[str, str] = {}
        self._heartbeat: asyncio.Task[None] | None = None

    async def setup_hook(self) -> None:
        self.config.data_dir.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(migrate, self.config.database_url)
        self.database = Database(self.config.database_url)
        self.scheduler = Scheduler(self.database)
        self.events = EventBus(self.database)
        from app.panels.botsettings import CoreCommands
        guild = discord.Object(id=self.config.guild_id)
        await self.add_cog(CoreCommands(self), guild=guild)
        self.module_states = await load_modules(self)
        synced = await self.tree.sync(guild=guild)
        logger.info("Синхронизировано серверных команд: %s", len(synced))
        self.scheduler.start()
        self.events.start()
        self._heartbeat = asyncio.create_task(self._health_loop(), name="newsqad-health")

    async def _health_loop(self) -> None:
        while True:
            try:
                if self.database is not None:
                    healthy = await self.database.ping()
                else:
                    healthy = False
                self._write_health(bool(self.is_ready() and healthy and self.get_guild(self.config.guild_id)))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Ошибка проверки состояния")
                self._write_health(False)
            await asyncio.sleep(20)

    def _write_health(self, ready: bool) -> None:
        path = Path(self.config.data_dir) / "health.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"ready": ready, "timestamp": time.time(), "version": VERSION}), encoding="utf-8")
        temporary.replace(path)

    async def on_ready(self) -> None:
        guild = self.get_guild(self.config.guild_id)
        if guild is None:
            logger.error("Бот не найден на целевом сервере NewSquad (%s)", self.config.guild_id)
        else:
            logger.info("NewSqadBot v%s подключён к %s (%s)", VERSION, guild.name, guild.id)
        for other in list(self.guilds):
            if other.id != self.config.guild_id:
                logger.warning("Бот обнаружен на чужом сервере %s: выход", other.id)
                try:
                    await other.leave()
                except discord.HTTPException:
                    logger.exception("Ошибка выхода из чужого сервера %s", other.id)

    async def on_guild_join(self, guild: discord.Guild) -> None:
        if guild.id != self.config.guild_id:
            logger.warning("Попытка добавления на другой сервер %s", guild.id)
            try:
                await guild.leave()
            except discord.HTTPException:
                logger.exception("Не удалось выйти из чужого сервера")

    async def on_disconnect(self) -> None:
        self._write_health(False)

    async def close(self) -> None:
        if self._heartbeat is not None:
            self._heartbeat.cancel()
            try:
                await self._heartbeat
            except asyncio.CancelledError:
                pass
            self._heartbeat = None
        if self.scheduler is not None:
            await self.scheduler.stop()
        if self.events is not None:
            await self.events.stop()
        if self.database is not None:
            await self.database.close()
        self._write_health(False)
        await super().close()
