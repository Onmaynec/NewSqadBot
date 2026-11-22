"""Жизненный цикл NewSqadBot и только серверная регистрация команд."""
from __future__ import annotations

import asyncio
import json
import logging
import tempfile
import time
from pathlib import Path

import discord
from discord.ext import commands
from sqlalchemy.engine import make_url

from app.config import VERSION, Config
from app.core.database import Database, migrate
from app.core.errors import GuildCommandTree
from app.core.event_bus import EventBus
from app.core.module_loader import load_modules
from app.core.observability import OperationalJournal
from app.core.scheduler import Scheduler
from app.ops.automatic_backup import BackupSettings, BackupWorker

logger = logging.getLogger(__name__)


class NewSqadBot(commands.Bot):
    def __init__(self, config: Config):
        intents = discord.Intents.none()
        intents.guilds = True
        intents.members = True
        intents.voice_states = True
        intents.guild_messages = True
        intents.message_content = config.message_content_intent
        intents.presences = config.presence_intent
        super().__init__(command_prefix=commands.when_mentioned, intents=intents,
                         tree_cls=GuildCommandTree, allowed_mentions=discord.AllowedMentions.none())
        self.config = config
        self.database: Database | None = None
        self.scheduler: Scheduler | None = None
        self.events: EventBus | None = None
        self.module_states: dict[str, str] = {}
        self._heartbeat: asyncio.Task[None] | None = None
        self._startup_finished = False
        self._web_runner = None
        self.operations = OperationalJournal(config.data_dir / "operations.json")
        self.backups: BackupWorker | None = None
        # discord.py handles its own retry/backoff; count only surfaced HTTP errors.
        original_request = self.http.request

        async def observe_discord_request(*args, **kwargs):
            try:
                return await original_request(*args, **kwargs)
            except discord.HTTPException as exc:
                self.operations.http_failure(int(getattr(exc, "status", 0)))
                raise

        self.http.request = observe_discord_request

    async def setup_hook(self) -> None:
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(self._asyncio_error)
        logger.info("Начало инициализации: version=%s guild_id=%s", VERSION, self.config.guild_id)
        self.config.data_dir.mkdir(parents=True, exist_ok=True)
        # Реальная проверка записи: os.access ненадёжен для Docker volumes.
        with tempfile.TemporaryFile(dir=self.config.data_dir):
            pass
        self._write_health(False, phase="starting")
        logger.info("SQLite: запуск Alembic и проверка целостности")
        await asyncio.to_thread(migrate, self.config.database_url)
        self.database = Database(self.config.database_url)
        await self.database.check_integrity()
        logger.info("SQLite: миграции и проверка целостности завершены")
        self.scheduler = Scheduler(self.database)
        self.events = EventBus(self.database)
        from app.panels.botsettings import CoreCommands
        guild = discord.Object(id=self.config.guild_id)
        await self.add_cog(CoreCommands(self), guild=guild)
        self.module_states = await load_modules(self)
        for name, state in self.module_states.items():
            if state == "error":
                logger.error("Модуль не загружен: %s", name)
                self.operations.record("module.load_failed", module=name)
        # Системные команды загружаются отдельно от модулей сообщества.
        # Справка собирает их вместе с командами всех активных модулей.
        for extension in ("app.modules.announcements", "app.modules.announcements_manager", "app.modules.help_center"):
            await self.load_extension(extension)
            logger.info("Системный модуль загружен: %s", extension)
        logger.info("Модули: %s", self.module_states)
        synced = await self.tree.sync(guild=guild)
        logger.info("Синхронизировано серверных команд: %s", len(synced))
        self.scheduler.start()
        self.events.start()
        path = make_url(self.config.database_url).database
        if not path:
            raise RuntimeError("Неизвестный путь SQLite для резервного копирования")
        self.backups = BackupWorker(Path(path), BackupSettings.from_env())
        self.backups.start()
        self._startup_finished = True
        self._heartbeat = asyncio.create_task(self._health_loop(), name="newsqad-health")
        from app.web.server import start_server

        self._web_runner = await start_server(
            self, password_hash=self.config.web_admin_password_hash,
            bind=self.config.web_admin_bind, port=self.config.web_admin_port,
        )

    @staticmethod
    def _asyncio_error(loop: asyncio.AbstractEventLoop, context: dict) -> None:
        exc = context.get("exception")
        logger.error("Фоновая ошибка asyncio: %s; task=%s", context.get("message"),
                     context.get("task", context.get("future")),
                     exc_info=(type(exc), exc, exc.__traceback__) if exc else None)

    async def on_error(self, event_method: str, *args, **kwargs) -> None:
        logger.exception("Ошибка обработки Discord-события: event=%s guild_id=%s",
                         event_method, self.config.guild_id)

    async def _health_loop(self) -> None:
        while True:
            try:
                if self._startup_finished:
                    if self.scheduler is not None and not self.scheduler.running:
                        logger.error("Обнаружена остановка Scheduler; выполняется перезапуск")
                        self.scheduler.start()
                        self.operations.record("worker.scheduler_restarted")
                    if self.events is not None and not self.events.running:
                        logger.error("Обнаружена остановка EventBus; выполняется перезапуск")
                        self.events.start()
                        self.operations.record("worker.event_bus_restarted")
                sqlite_ok = self.database is not None and await self.database.ping()
                discord_ok = bool(self.is_ready() and self.get_guild(self.config.guild_id))
                workers_ok = bool(self.scheduler and self.scheduler.running
                                  and self.events and self.events.running)
                ready = bool(self._startup_finished and sqlite_ok and discord_ok and workers_ok)
                self._write_health(ready, phase="ready" if ready else "degraded",
                                   discord_ok=discord_ok, sqlite_ok=sqlite_ok)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Ошибка проверки состояния")
                self._write_health(False, phase="health_error")
            await asyncio.sleep(20)

    def _write_health(self, ready: bool, *, phase: str = "stopped",
                      discord_ok: bool = False, sqlite_ok: bool = False) -> None:
        path = Path(self.config.data_dir) / "health.json"
        temporary = path.with_suffix(".tmp")
        payload = {
            "ready": ready, "timestamp": time.time(), "version": VERSION, "phase": phase,
            "discord": discord_ok, "sqlite": sqlite_ok,
            "scheduler": bool(self.scheduler and self.scheduler.running),
            "event_bus": bool(self.events and self.events.running),
            "module_errors": [key for key, status in self.module_states.items() if status == "error"],
            "gateway_last_connected_at": self.operations.last_connected_at,
            "gateway_last_disconnected_at": self.operations.last_disconnected_at,
            "discord_http_errors": self.operations.http_errors,
            "discord_http_rate_limits": self.operations.http_rate_limits,
            "database_metrics": self.database.query_metrics() if self.database else {},
            "backup": self.backups.status() if self.backups else {},
        }
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)

    async def on_interaction(self, interaction: discord.Interaction) -> None:
        """Диагностика потерянных форм после reconnect/restart или истечения UI-сессии."""
        if interaction.type != discord.InteractionType.modal_submit:
            return
        data = interaction.data or {}
        custom_id = data.get("custom_id", "")
        if not isinstance(custom_id, str) or not custom_id.startswith("newsqad:tickets:create:"):
            return
        logger.info("Получен modal_submit тикета interaction=%s user=%s", interaction.id, interaction.user.id)
        # После перезапуска старый Modal уже не зарегистрирован в памяти.
        # Даём обычному обработчику короткое время подтвердить запрос.
        await asyncio.sleep(2.0)
        if interaction.response.is_done():
            return
        logger.warning("Форма тикета осталась без обработчика interaction=%s (возможен перезапуск)", interaction.id)
        try:
            await interaction.response.send_message(
                "Форма больше не активна. Откройте панель поддержки и отправьте заявку заново.",
                ephemeral=True,
            )
        except (discord.HTTPException, discord.InteractionResponded):
            logger.exception("Не удалось ответить на неподтверждённую форму interaction=%s", interaction.id)

    async def on_ready(self) -> None:
        self.operations.record("gateway.connected")
        # on_ready вызывается повторно после reconnect. Не дублируем задачи.
        if self._startup_finished:
            self.scheduler.start()
            self.events.start()
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

    async def on_resumed(self) -> None:
        self.operations.record("gateway.resumed")
        if self._startup_finished:
            self.scheduler.start()
            self.events.start()
        logger.info("Discord Gateway: сессия восстановлена")

    async def on_guild_join(self, guild: discord.Guild) -> None:
        if guild.id != self.config.guild_id:
            logger.warning("Попытка добавления на другой сервер %s", guild.id)
            try:
                await guild.leave()
            except discord.HTTPException:
                logger.exception("Не удалось выйти из чужого сервера")

    async def on_disconnect(self) -> None:
        self.operations.record("gateway.disconnected")
        self._write_health(False, phase="discord_disconnected")

    async def close(self) -> None:
        self._startup_finished = False
        self._write_health(False, phase="stopping")
        if self._heartbeat is not None:
            self._heartbeat.cancel()
            try:
                await self._heartbeat
            except asyncio.CancelledError:
                pass
            self._heartbeat = None
        if self._web_runner is not None:
            runner, self._web_runner = self._web_runner, None
            await runner.cleanup()
        if self.backups is not None:
            await self.backups.stop()
        if self.scheduler is not None:
            await self.scheduler.stop()
        if self.events is not None:
            await self.events.stop()
        if self.database is not None:
            await self.database.close()
        self._write_health(False)
        await super().close()
