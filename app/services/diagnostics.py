"""Диагностика настройки NewSqadBot без изменения ролей и каналов."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import discord

from app.ui.theme import card


@dataclass(frozen=True, slots=True)
class Diagnostic:
    level: str
    name: str
    description: str


def timestamp_utc(value: int | None) -> str:
    if not value:
        return "нет"
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except (OverflowError, ValueError, OSError):
        return "неизвестно"


async def collect(bot) -> list[Diagnostic]:
    result: list[Diagnostic] = []
    guild = bot.get_guild(bot.config.guild_id)
    journal = getattr(bot, "operations", None)
    if journal is not None:
        state = journal.snapshot()
        connected = state["last_connected_at"]
        disconnected = state["last_disconnected_at"]
        online = bool(getattr(bot, "is_ready", lambda: False)() and guild)
        result.append(Diagnostic(
            "ok" if online else "warning", "Discord Gateway",
            f"Состояние: {'подключён' if online else 'отключён'}; "
            f"последнее подключение: {timestamp_utc(connected)}; "
            f"последний обрыв: {timestamp_utc(disconnected)}."))
        result.append(Diagnostic(
            "warning" if state["http_errors"] else "ok", "Ошибки Discord HTTP",
            f"Ошибок после начала учёта: {state['http_errors']}; "
            f"HTTP 429 после исчерпания повторов: {state['http_rate_limits']}; "
            f"последний статус: {state['last_http_status'] or 'нет'}."))
        history = state["history"][-6:]
        description = "; ".join(
            f"{timestamp_utc(item['at'])} {item['kind']}" +
            (f" ({item['module']})" if item.get("module") else "") for item in history)
        result.append(Diagnostic("ok", "Журнал восстановления", description or "Событий ещё не было."))
    db_metrics = getattr(bot.database, "query_metrics", None) if getattr(bot, "database", None) else None
    if callable(db_metrics):
        values = db_metrics()
        result.append(Diagnostic(
            "warning" if values["slow_queries"] or values["failed_queries"] else "ok",
            "SQLite: время запросов",
            f"Запросов: {values['queries']}; медленных (от 500 мс): {values['slow_queries']}; "
            f"ошибок: {values['failed_queries']}; последний: {values['last_ms']} мс; "
            f"максимум: {values['max_ms']} мс."))
    backups = getattr(bot, "backups", None)
    if backups is not None:
        backup = backups.status()
        healthy = backup["running"] and backup["last_success"] and not backup["last_error"]
        result.append(Diagnostic(
            "ok" if healthy else "warning", "Автоматические копии",
            f"Работает: {backup['running']}; последняя проверенная копия: "
            f"{timestamp_utc(backup['last_verified'])}; "
            f"экспорт вне volume: {timestamp_utc(backup['last_export'])}; "
            f"ошибок: {backup['failures']}; причина: {backup['last_error'] or 'нет'}."))
    if guild is None:
        result.append(Diagnostic("error", "Сервер", "Бот не видит целевой сервер."))
        return result
    try:
        ready = await bot.database.ping()
    except Exception:
        ready = False
    result.append(Diagnostic("ok" if ready else "error", "SQLite",
                             "База отвечает." if ready else "База недоступна."))
    if ready and hasattr(bot.database, "check_integrity"):
        try:
            await bot.database.check_integrity()
            result.append(Diagnostic("ok", "Целостность SQLite", "quick_check и foreign_key_check пройдены."))
        except Exception:
            result.append(Diagnostic("error", "Целостность SQLite", "Обнаружена ошибка; проверьте резервную копию."))
    for name, process in (("Scheduler", getattr(bot, "scheduler", None)), ("EventBus", getattr(bot, "events", None))):
        if process is None or not process.running:
            result.append(Diagnostic("error", name, "Фоновый процесс не запущен."))
            continue
        try:
            state = await process.status()
        except Exception:
            result.append(Diagnostic("error", name, "Не удалось получить состояние очереди."))
            continue
        if name == "Scheduler":
            problems = state["failed"] + state["missing_handlers"]
            pending = state["pending"] + state["running"]
            details = (f"Ожидают: {pending}; просрочены: {state['overdue']}; "
                       f"зависшие leases: {state['stale']}; ошибки: {state['failed']}; "
                       f"без обработчика: {state['missing_handlers']}.")
            level = "warning" if problems or state["overdue"] > 50 or state["stale"] else "ok"
        else:
            details = (f"В очереди: {state['queued']}; квитанций: {state['receipts']}; "
                       f"типы без обработчика: {state['unhandled_kinds']}.")
            level = "warning" if state["unhandled_kinds"] or state["queued"] > 500 else "ok"
        if process.failures:
            details += f" Ошибок с момента запуска: {process.failures}; последняя: {process.last_error}."
            level = "warning"
        result.append(Diagnostic(level, name, details))
    failed = [name for name, status in getattr(bot, "module_states", {}).items() if status == "error"]
    if failed:
        result.append(Diagnostic("warning", "Модули", "Ошибки загрузки: " + ", ".join(failed)))
    candidate_id = await bot.database.get_setting(guild.id, "candidate_role_id")
    candidate_role = guild.get_role(candidate_id) if candidate_id else None
    result.append(Diagnostic(
        "ok" if candidate_role else "error", "Роль кандидата",
        "Правило доступа: отсутствие этой роли." if candidate_role
        else "Настройте корректную роль кандидата в /botsettings.",
    ))
    me = guild.me
    if me is None:
        result.append(Diagnostic("error", "Права бота", "Не найден участник-бот."))
    else:
        perms = me.guild_permissions
        result.append(Diagnostic(
            "ok" if perms.manage_roles else "warning", "Управление ролями",
            "Доступно." if perms.manage_roles else "Нет Manage Roles.",
        ))
        if candidate_role and me.top_role <= candidate_role:
            result.append(Diagnostic(
                "error", "Иерархия ролей",
                "Роль бота должна быть выше роли кандидата.",
            ))
    channels = [
        ("audit_channel_id", "Журнал событий", discord.TextChannel, False),
        ("moderation_audit_channel_id", "Журнал модерации", discord.TextChannel, False),
        ("application_channel_id", "Канал заявок", discord.TextChannel, False),
        ("review_channel_id", "Рассмотрение заявок", discord.TextChannel, False),
        ("voice_lobby_channel_id", "Канал генерации", discord.VoiceChannel, False),
        ("voice_category_channel_id", "Категория голосовых комнат",
         discord.CategoryChannel, True),
        ("ticket_category_channel_id", "Категория тикетов",
         discord.CategoryChannel, True),
    ]
    for key, label, expected, category in channels:
        channel_id = await bot.database.get_setting(guild.id, key)
        channel = guild.get_channel(channel_id) if channel_id else None
        if channel is None:
            result.append(Diagnostic("warning", label, "Не настроен или удалён."))
            continue
        if not isinstance(channel, expected):
            result.append(Diagnostic("error", label, "Выбран неверный тип канала."))
            continue
        if me is None:
            continue
        perms = channel.permissions_for(me)
        if category:
            allowed = perms.manage_channels
        elif expected is discord.VoiceChannel:
            allowed = perms.view_channel and perms.move_members
        else:
            allowed = perms.view_channel and perms.send_messages and perms.embed_links
        if not allowed:
            result.append(Diagnostic("warning", label, "Недостаточно разрешений в канале."))
        else:
            result.append(Diagnostic("ok", label, "Конфигурация доступна."))
    return result


def diagnostic_embed(items: list[Diagnostic]) -> discord.Embed:
    marker = {"ok": "✓", "warning": "!", "error": "×"}
    errors = sum(x.level == "error" for x in items)
    warnings = sum(x.level == "warning" for x in items)
    details = "\n".join(
        f"**{marker.get(item.level, '?')}  {item.name}** — {item.description}"
        for item in items
    )
    return card("NewSquad / Диагностика конфигурации",
                f"**Критических ошибок:** {errors}  ·  **Предупреждений:** {warnings}\n\n"
                f"{details}"[:4000])
