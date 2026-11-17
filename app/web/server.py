"""Однопроцессная web-консоль: локальный порт, пароль, CSRF, whitelist настроек.

Нет OAuth, удалённого хостинга, SQL API или редактирования произвольных ключей.
Discord данные берутся из работающего бота; секреты из .env никогда не возвращаются.
"""
from __future__ import annotations

import hmac
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from aiohttp import web
from sqlalchemy import select

from app.config import VERSION
from app.models import GuildSetting
from app.services.diagnostics import collect
from app.services.role_rules import DANGEROUS_PERMISSION_NAMES
from app.web.auth import SessionStore, validate_hash, verify_password
from app.web.catalog import BY_KEY, GROUPS, SETTINGS, Setting

log = logging.getLogger(__name__)
ROOT = Path(__file__).parent / "static"
COOKIE = "nsb_session"
MAX_JSON = 8192


@dataclass(slots=True)
class PanelState:
    bot: object
    password_hash: str
    sessions: SessionStore


STATE = web.AppKey("state", PanelState)


def local_host(value: str) -> bool:
    """Защита от DNS rebinding: только точные localhost/127.0.0.1/[::1]."""
    if not value or any(ch in value for ch in "\r\n/@"):
        return False
    try:
        parsed = urlsplit(f"http://{value}")
        return parsed.hostname in ("localhost", "127.0.0.1", "::1") and (
            parsed.port is None or 1 <= parsed.port <= 65535)
    except ValueError:
        return False


@web.middleware
async def security_headers(request: web.Request, handler):
    host = request.headers.get("Host", "")
    if not local_host(host):
        response = web.json_response({"error": "Недопустимый адрес сайта."}, status=421)
    elif request.method not in ("GET", "HEAD"):
        # Браузерные POST/fetch обязаны прислать точный Origin.
        if request.headers.get("Origin") != f"http://{host}":
            response = web.json_response({"error": "Запрос с другого сайта запрещён."}, status=403)
        else:
            try:
                response = await handler(request)
            except web.HTTPException as exc:
                response = web.Response(status=exc.status, text=exc.text, content_type=exc.content_type)
    else:
        try:
            response = await handler(request)
        except web.HTTPException as exc:
            response = web.Response(status=exc.status, text=exc.text, content_type=exc.content_type)
    response.headers.update({
        "Content-Security-Policy": (
            "default-src 'none'; script-src 'self'; style-src 'self'; "
            "img-src 'self'; connect-src 'self'; font-src 'self'; "
            "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"),
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
        "Cache-Control": "no-store",
        "Cross-Origin-Resource-Policy": "same-origin",
    })
    return response


def state(request: web.Request) -> PanelState:
    return request.app[STATE]


def require_auth(request: web.Request):
    record = state(request).sessions.get(request.cookies.get(COOKIE))
    if record is None:
        raise web.HTTPUnauthorized(text="Требуется вход в панель.")
    return record


def require_csrf(request: web.Request) -> None:
    record = require_auth(request)
    supplied = request.headers.get("X-CSRF-Token", "")
    if not supplied or not hmac.compare_digest(supplied, record.csrf):
        raise web.HTTPForbidden(text="Неверный токен подтверждения запроса.")


async def json_payload(request: web.Request) -> dict:
    if request.content_type != "application/json":
        raise web.HTTPUnsupportedMediaType(text="Нужен application/json.")
    if request.content_length is not None and request.content_length > MAX_JSON:
        raise web.HTTPRequestEntityTooLarge(max_size=MAX_JSON, actual_size=request.content_length)
    try:
        value = await request.json()
    except (ValueError, UnicodeDecodeError) as exc:
        raise web.HTTPBadRequest(text="Неверный JSON.") from exc
    if not isinstance(value, dict):
        raise web.HTTPBadRequest(text="Ожидается JSON-объект.")
    return value


async def index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(ROOT / "index.html")


async def static_file(request: web.Request) -> web.FileResponse:
    name = request.match_info["name"]
    if name not in ("app.js", "style.css"):
        raise web.HTTPNotFound()
    return web.FileResponse(ROOT / name)


async def session_info(request: web.Request) -> web.Response:
    session = state(request).sessions.get(request.cookies.get(COOKIE))
    return web.json_response({"authenticated": bool(session),
                              "csrf": session.csrf if session else None})


async def login(request: web.Request) -> web.Response:
    panel = state(request)
    peer = request.remote or "unknown"
    if panel.sessions.limited(peer):
        return web.json_response({"error": "Слишком много попыток. Повторите через 5 минут."}, status=429)
    payload = await json_payload(request)
    password = payload.get("password", "")
    if not isinstance(password, str) or not verify_password(password, panel.password_hash):
        panel.sessions.fail(peer)
        log.warning("Отказ во входе в локальную панель (IP не регистрируется)")
        return web.json_response({"error": "Неверный пароль."}, status=401)
    panel.sessions.failures.pop(peer, None)
    token, record = panel.sessions.create()
    response = web.json_response({"authenticated": True, "csrf": record.csrf})
    response.set_cookie(COOKIE, token, httponly=True, secure=False,
                        samesite="Strict", max_age=4 * 3600, path="/")
    log.info("Успешный вход в локальную административную панель")
    return response


async def logout(request: web.Request) -> web.Response:
    require_csrf(request)
    state(request).sessions.revoke(request.cookies.get(COOKIE))
    response = web.json_response({"ok": True})
    response.del_cookie(COOKIE, path="/")
    return response


def guild_for(bot):
    guild = bot.get_guild(bot.config.guild_id)
    if not bot.is_ready() or guild is None:
        return None
    return guild


def serialize_role(role: discord.Role) -> dict:
    return {
        "id": role.id, "name": role.name, "position": role.position,
        "managed": role.managed,
        "dangerous": any(getattr(role.permissions, key, False)
                         for key in DANGEROUS_PERMISSION_NAMES),
    }


def channel_type(channel) -> str:
    if isinstance(channel, discord.TextChannel):
        return "text"
    if isinstance(channel, discord.VoiceChannel):
        return "voice"
    if isinstance(channel, discord.CategoryChannel):
        return "category"
    return "other"


def serialize_channel(channel) -> dict:
    return {"id": channel.id, "name": channel.name, "type": channel_type(channel),
            "category": channel.category.name if getattr(channel, "category", None) else ""}


async def read_all_settings(db, guild_id: int) -> dict:
    """Один SELECT на все параметры вместо N запросов в SQLite."""
    async with db.sessions() as session:
        entries = (await session.scalars(select(GuildSetting).where(
            GuildSetting.guild_id == guild_id))).all()
    return {entry.key: json.loads(entry.value_json) for entry in entries}


async def bootstrap(request: web.Request) -> web.Response:
    require_auth(request)
    bot = state(request).bot
    try:
        raw = await read_all_settings(bot.database, bot.config.guild_id)
    except Exception:
        log.exception("Не удалось прочитать настройки web-консоли")
        return web.json_response({"error": "SQLite временно недоступна."}, status=503)
    guild = guild_for(bot)
    settings = []
    for spec in SETTINGS:
        value = raw.get(spec.key, spec.default)
        warning = ""
        if guild and spec.kind == "role" and value and guild.get_role(value) is None:
            warning = "Роль удалена из Discord."
        if guild and spec.kind == "channel" and value and guild.get_channel(value) is None:
            warning = "Канал удалён из Discord."
        settings.append({**spec.public(), "value": value, "configured": spec.key in raw,
                         "warning": warning})
    roles = sorted((serialize_role(role) for role in guild.roles if not role.is_default()),
                   key=lambda x: (-x["position"], x["name"])) if guild else []
    channels = sorted((serialize_channel(ch) for ch in guild.channels if channel_type(ch) != "other"),
                      key=lambda x: (x["type"], x["category"], x["name"])) if guild else []
    return web.json_response({
        "version": VERSION, "guild_id": bot.config.guild_id,
        "guild_name": guild.name if guild else "NewSquad",
        "discord_ready": guild is not None,
        "modules": bot.module_states,
        "groups": GROUPS, "settings": settings, "roles": roles, "channels": channels,
        "total_configured": sum(spec.key in raw for spec in SETTINGS),
    })


def _check_role(guild, key: str, value: int) -> None:
    role = guild.get_role(value)
    if role is None or role.is_default() or role.managed:
        raise ValueError("Роль не существует или управляется Discord.")
    # Эти роли бот назначает автоматически, поэтому важна иерархия и отсутствие опасных прав.
    if key in ("member_role_id", "candidate_role_id", "female_role_id"):
        bot_member = guild.me
        if bot_member is None or not bot_member.guild_permissions.manage_roles or bot_member.top_role <= role:
            raise ValueError("Бот не может управлять этой ролью. Проверьте иерархию ролей.")
        if any(getattr(role.permissions, name, False) for name in DANGEROUS_PERMISSION_NAMES):
            raise ValueError("Автоматическая роль имеет опасные Discord-разрешения.")


def _check_channel(guild, spec: Setting, value: int) -> None:
    channel = guild.get_channel(value)
    if channel is None or channel_type(channel) != spec.channel_kind:
        raise ValueError("Канал не существует или имеет неверный тип.")
    member = guild.me
    if member is None:
        raise ValueError("Нет сведений о правах бота в Discord.")
    permissions = channel.permissions_for(member)
    if not permissions.view_channel:
        raise ValueError("У бота нет View Channel в этом канале.")
    if spec.channel_kind == "category" and not permissions.manage_channels:
        raise ValueError("Боту необходимо Manage Channels в этой категории.")
    if spec.channel_kind == "text" and spec.key not in ("rules_channel_id",):
        if not permissions.send_messages or not permissions.embed_links:
            raise ValueError("Боту нужны Send Messages и Embed Links в этом канале.")


def validate_value(spec: Setting, value, guild):
    """Типизированный allowlist; bool не должен быть принят как int."""
    if spec.kind in ("role", "channel"):
        if value is None:
            return None
        if type(value) is not int or value <= 0:
            raise ValueError("Ожидается Discord ID выбранного объекта.")
        if guild is None:
            raise ValueError("Discord недоступен: выбор роли/канала временно запрещён.")
        if spec.kind == "role":
            _check_role(guild, spec.key, value)
        else:
            _check_channel(guild, spec, value)
        return value
    if spec.kind == "bool":
        if type(value) is not bool:
            raise ValueError("Ожидается переключатель да/нет.")
        return value
    if spec.kind == "int":
        if type(value) is not int or not spec.minimum <= value <= spec.maximum:
            raise ValueError(f"Допустимое целое число: {spec.minimum}–{spec.maximum}.")
        return value
    if spec.kind == "choice":
        if type(value) is not str or value not in {item[0] for item in spec.choices}:
            raise ValueError("Неизвестный вариант.")
        return value
    if spec.kind == "timezone":
        if type(value) is not str or not 1 <= len(value) <= 64:
            raise ValueError("Некорректный часовой пояс.")
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Такого часового пояса IANA нет.") from exc
        return value
    if spec.kind == "channel_list":
        if guild is None:
            raise ValueError("Discord недоступен.")
        if not isinstance(value, list) or len(value) > 40 or any(type(item) is not int for item in value):
            raise ValueError("Ожидается список до 40 каналов.")
        if any(guild.get_channel(item) is None or channel_type(guild.get_channel(item)) != "text"
               for item in value):
            raise ValueError("Один из выбранных каналов удалён или недоступен.")
        return list(dict.fromkeys(value))
    raise ValueError("Неподдерживаемый тип настройки.")


async def save_setting(request: web.Request) -> web.Response:
    require_csrf(request)
    spec = BY_KEY.get(request.match_info.get("key"))
    if spec is None:
        raise web.HTTPNotFound(text="Настройка отсутствует в разрешённом списке.")
    payload = await json_payload(request)
    if set(payload) != {"value"}:
        raise web.HTTPBadRequest(text="Изменять можно только поле value.")
    bot = state(request).bot
    try:
        value = validate_value(spec, payload["value"], guild_for(bot))
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=422)
    try:
        # actor_id=0 означает действие локального администратора (не Discord ID).
        # Database.set_setting создаёт запись в system_audit без значения настройки.
        await bot.database.set_setting(bot.config.guild_id, spec.key, value, actor_id=0)
    except Exception:
        log.exception("Сохранение параметра %s не выполнено", spec.key)
        return web.json_response({"error": "Не удалось сохранить: проверьте SQLite."}, status=503)
    log.info("Web: параметр %s изменён локальным администратором", spec.key)
    return web.json_response({"ok": True, "key": spec.key, "value": value,
                              "restart_required": spec.restart_required})


async def diagnostics(request: web.Request) -> web.Response:
    require_auth(request)
    bot = state(request).bot
    if guild_for(bot) is None:
        return web.json_response({"ready": False, "items": [{
            "level": "warning", "name": "Discord", "description": "Шлюз Discord не готов."}]})
    try:
        items = await collect(bot)
        return web.json_response({"ready": True, "items": [
            {"level": x.level, "name": x.name, "description": x.description} for x in items]})
    except Exception:
        log.exception("Диагностика web-консоли недоступна")
        return web.json_response({"error": "Ошибка диагностики. Смотрите Docker-логи."}, status=503)


def create_app(bot, password_hash: str) -> web.Application:
    if not validate_hash(password_hash):
        raise ValueError("WEB_ADMIN_PASSWORD_HASH некорректен: выполните python -m app.web.auth hash")
    app = web.Application(middlewares=[security_headers], client_max_size=MAX_JSON)
    app[STATE] = PanelState(bot=bot, password_hash=password_hash, sessions=SessionStore())
    app.router.add_get("/", index)
    app.router.add_get("/static/{name}", static_file)
    app.router.add_get("/api/session", session_info)
    app.router.add_post("/api/login", login)
    app.router.add_post("/api/logout", logout)
    app.router.add_get("/api/bootstrap", bootstrap)
    app.router.add_get("/api/diagnostics", diagnostics)
    app.router.add_post("/api/settings/{key}", save_setting)
    return app


async def start_server(bot, *, password_hash: str, bind: str, port: int) -> web.AppRunner:
    if bind not in ("127.0.0.1", "0.0.0.0") or not 1 <= port <= 65535:
        raise ValueError("Неверный локальный адрес или порт панели.")
    runner = web.AppRunner(create_app(bot, password_hash), access_log=None)
    await runner.setup()
    try:
        site = web.TCPSite(runner, host=bind, port=port)
        await site.start()
    except Exception:
        await runner.cleanup()
        raise
    log.info("Локальная панель включена: bind=%s port=%s (Docker host: localhost)", bind, port)
    return runner
