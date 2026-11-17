"""Однопроцессная web-консоль: локальный порт, пароль, CSRF, whitelist настроек.

Поддерживает отдельный Discord OAuth на публичном HTTPS и локальный пароль без OAuth.
Discord данные берутся из работающего бота; секреты из .env никогда не возвращаются.
"""
from __future__ import annotations

import hmac
import json
import logging
import re
from dataclasses import dataclass, field
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
from app.web.oauth import authorize_url, consume_state, exchange_code, new_state, permission

log = logging.getLogger(__name__)
ROOT = Path(__file__).parent / "static"
COOKIE = "nsb_session"
MAX_JSON = 8192


@dataclass(slots=True)
class PanelState:
    bot: object
    password_hash: str
    sessions: SessionStore
    discord_client_id: str = ""
    discord_client_secret: str = ""
    public_url: str = ""
    pending_states: dict[str, float] = field(default_factory=dict)


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
    panel = state(request)
    public_host = urlsplit(panel.public_url).netloc if panel.public_url else ""
    if not local_host(host) and host != public_host:
        response = web.json_response({"error": "Недопустимый адрес сайта."}, status=421)
    elif request.method not in ("GET", "HEAD"):
        # Никакой обработки POST от произвольных интернет-сайтов.
        expected = panel.public_url if host == public_host else f"http://{host}"
        if request.headers.get("Origin") != expected:
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
            "img-src 'self' https://cdn.discordapp.com https://media.discordapp.net; connect-src 'self'; font-src 'self'; "
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


async def require_role(request: web.Request, role: str = "support"):
    session = require_auth(request)
    if session.user_id is None:
        if state(request).discord_client_id:
            raise web.HTTPUnauthorized(text="Войдите через Discord.")
        # Пароль для localhost является локальным владельцем.
        if not local_host(request.headers.get("Host", "")):
            raise web.HTTPForbidden(text="Парольный вход разрешён только локально.")
        return "owner", None
    return await permission(state(request).bot, session.user_id, role)


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
    if name not in ("app.js", "portal.js", "style.css"):
        raise web.HTTPNotFound()
    return web.FileResponse(ROOT / name)


async def session_info(request: web.Request) -> web.Response:
    panel = state(request)
    session = panel.sessions.get(request.cookies.get(COOKIE))
    if session is None:
        return web.json_response({"authenticated": False, "csrf": None,
                                  "auth_method": "discord" if panel.discord_client_id else "password"})
    try:
        role, member = await require_role(request)
    except web.HTTPException:
        return web.json_response({"authenticated": False, "csrf": None,
                                  "auth_method": "discord" if panel.discord_client_id else "password"})
    return web.json_response({
        "authenticated": True, "csrf": session.csrf, "role": role,
        "user": {"id": str(member.id), "name": member.display_name,
                 "avatar_url": str(member.display_avatar.url)} if member else None,
        "auth_method": "discord" if panel.discord_client_id else "password",
    })


async def login(request: web.Request) -> web.Response:
    panel = state(request)
    if panel.discord_client_id:
        raise web.HTTPForbidden(text="Вход по паролю выключен. Используйте Discord.")
    if not local_host(request.headers.get("Host", "")):
        raise web.HTTPForbidden(text="Пароль доступен только локально.")
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



async def oauth_start(request: web.Request) -> web.Response:
    panel = state(request)
    if not panel.discord_client_id:
        raise web.HTTPNotFound()
    # Cookie must be issued from public HTTPS origin: localhost cookie cannot
    # return to a distinct ts.net callback host.
    host = request.headers.get("Host", "")
    if host != urlsplit(panel.public_url).netloc:
        return web.HTTPFound(location=panel.public_url + "/auth/discord")
    ticket = new_state(panel.pending_states)
    redirect = panel.public_url + "/auth/callback"
    location = authorize_url(panel.discord_client_id, redirect, ticket)
    response = web.HTTPFound(location=location)
    response.set_cookie("nsb_oauth_state", ticket, httponly=True, secure=True,
                        samesite="Lax", max_age=300, path="/auth")
    return response


async def oauth_callback(request: web.Request) -> web.Response:
    panel = state(request)
    if not panel.discord_client_id:
        raise web.HTTPNotFound()
    oauth_state = request.query.get("state")
    code = request.query.get("code")
    if not consume_state(panel.pending_states, oauth_state,
                         request.cookies.get("nsb_oauth_state")):
        raise web.HTTPForbidden(text="Сессия авторизации устарела или не совпадает.")
    if not isinstance(code, str) or not 1 <= len(code) <= 1024:
        raise web.HTTPBadRequest(text="Discord не прислал код авторизации.")
    try:
        uid = await exchange_code(panel.discord_client_id, panel.discord_client_secret,
                                  panel.public_url + "/auth/callback", code)
        await permission(panel.bot, uid, "support")
    except web.HTTPException:
        raise
    except (OSError, TimeoutError) as exc:
        log.warning("Discord OAuth временно недоступен")
        raise web.HTTPBadGateway(text="Discord временно недоступен.") from exc
    token, _ = panel.sessions.create(user_id=uid)
    response = web.HTTPFound(location=panel.public_url + "/")
    response.set_cookie(COOKIE, token, httponly=True, secure=True,
                        samesite="Lax", max_age=4 * 3600, path="/")
    response.del_cookie("nsb_oauth_state", path="/auth")
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
        "id": str(role.id), "name": role.name, "position": role.position,
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
    return {"id": str(channel.id), "name": channel.name, "type": channel_type(channel),
            "category": channel.category.name if getattr(channel, "category", None) else ""}


async def read_all_settings(db, guild_id: int) -> dict:
    """Один SELECT на все параметры вместо N запросов в SQLite."""
    async with db.sessions() as session:
        entries = (await session.scalars(select(GuildSetting).where(
            GuildSetting.guild_id == guild_id))).all()
    return {entry.key: json.loads(entry.value_json) for entry in entries}


async def bootstrap(request: web.Request) -> web.Response:
    role, member = await require_role(request)
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
        settings.append({**spec.public(), "value": public_value(spec, value),
                         "configured": spec.key in raw, "warning": warning})
    roles = sorted((serialize_role(role) for role in guild.roles if not role.is_default()),
                   key=lambda x: (-x["position"], x["name"])) if guild else []
    channels = sorted((serialize_channel(ch) for ch in guild.channels if channel_type(ch) != "other"),
                      key=lambda x: (x["type"], x["category"], x["name"])) if guild else []
    return web.json_response({
        "viewer_role": role,
        "viewer": {"id": str(member.id), "name": member.display_name,
                   "avatar_url": str(member.display_avatar.url)} if member else None,
        "version": VERSION, "guild_id": str(bot.config.guild_id),
        "guild_name": guild.name if guild else "NewSquad",
        "guild_icon_url": str(guild.icon.url) if guild and getattr(guild, "icon", None) else None,
        "guild_banner_url": str(guild.banner.url) if guild and getattr(guild, "banner", None) else None,
        "guild_member_count": getattr(guild, "member_count", None) if guild else None,
        "discord_ready": guild is not None,
        "modules": bot.module_states if role == "owner" else {},
        "groups": GROUPS if role == "owner" else (),
        "settings": settings if role == "owner" else (),
        "roles": roles if role == "owner" else (),
        "channels": channels if role == "owner" else (),
        "total_configured": sum(spec.key in raw for spec in SETTINGS) if role == "owner" else 0,
    })



def public_value(spec: Setting, value):
    """Snowflake — строка в JSON: JavaScript Number округляет 18–20-значные ID."""
    if spec.kind in ("role", "channel"):
        return str(value) if value is not None else None
    if spec.kind == "channel_list" and isinstance(value, list):
        return [str(item) for item in value]
    return value


def parse_snowflake(value) -> int:
    """Не допускаем float, отрицательные и округлённые значения JavaScript."""
    if type(value) is int:
        candidate = value
    elif isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,19}", value):
        candidate = int(value)
    else:
        raise ValueError("Некорректный Discord ID: используйте строку с цифрами.")
    if not 0 < candidate < 2**64:
        raise ValueError("Discord ID выходит за допустимый диапазон.")
    return candidate


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
        value = parse_snowflake(value)
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
        if not isinstance(value, list) or len(value) > 40:
            raise ValueError("Ожидается список до 40 каналов.")
        ids = [parse_snowflake(item) for item in value]
        if any(guild.get_channel(item) is None or channel_type(guild.get_channel(item)) != "text"
               for item in ids):
            raise ValueError("Один из выбранных каналов удалён или недоступен.")
        return list(dict.fromkeys(ids))
    raise ValueError("Неподдерживаемый тип настройки.")


async def save_setting(request: web.Request) -> web.Response:
    await require_role(request, "owner")
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
        await bot.database.set_setting(bot.config.guild_id, spec.key, value,
                                       actor_id=require_auth(request).user_id or 0)
    except Exception:
        log.exception("Сохранение параметра %s не выполнено", spec.key)
        return web.json_response({"error": "Не удалось сохранить: проверьте SQLite."}, status=503)
    log.info("Web: параметр %s изменён локальным администратором", spec.key)
    return web.json_response({"ok": True, "key": spec.key, "value": value,
                              "restart_required": spec.restart_required})



async def save_batch(request: web.Request) -> web.Response:
    """Проверить все изменения и зафиксировать одним commit; без частичных сохранений."""
    await require_role(request, "owner")
    require_csrf(request)
    payload = await json_payload(request)
    if set(payload) != {"group", "changes"}:
        raise web.HTTPBadRequest(text="Нужны group и changes.")
    group, changes = payload["group"], payload["changes"]
    if group not in GROUPS or not isinstance(changes, dict) or not 1 <= len(changes) <= 80:
        return web.json_response({"error": "Неверная группа или пустой список изменений."}, status=422)
    bot = state(request).bot
    guild = guild_for(bot)
    normalized = {}
    try:
        for key, raw_value in changes.items():
            spec = BY_KEY.get(key)
            if spec is None or spec.group != group:
                raise ValueError("Параметр недоступен в выбранном разделе.")
            normalized[key] = validate_value(spec, raw_value, guild)
    except ValueError as exc:
        return web.json_response({"error": f"{key}: {exc}"}, status=422)
    try:
        await bot.database.set_settings(bot.config.guild_id, normalized,
                                        actor_id=require_auth(request).user_id or 0)
    except Exception:
        log.exception("Не удалось атомарно сохранить раздел %s", group)
        return web.json_response({"error": "SQLite не смогла сохранить изменения. Ничего не применено."},
                                 status=503)
    log.info("Web: раздел %s сохранён одним commit, параметров: %d", group, len(normalized))
    return web.json_response({
        "ok": True, "values": {key: public_value(BY_KEY[key], value)
                               for key, value in normalized.items()},
        "count": len(normalized),
        "restart_required": any(BY_KEY[key].restart_required for key in normalized),
    })


async def diagnostics(request: web.Request) -> web.Response:
    await require_role(request, "admin")
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


def create_app(bot, password_hash: str, *, discord_client_id: str = "",
               discord_client_secret: str = "", public_url: str = "") -> web.Application:
    if not discord_client_id and not validate_hash(password_hash):
        raise ValueError("WEB_ADMIN_PASSWORD_HASH некорректен: выполните python -m app.web.auth hash")
    app = web.Application(middlewares=[security_headers], client_max_size=MAX_JSON)
    app[STATE] = PanelState(bot=bot, password_hash=password_hash, sessions=SessionStore(),
                            discord_client_id=discord_client_id,
                            discord_client_secret=discord_client_secret, public_url=public_url)
    app.router.add_get("/", index)
    app.router.add_get("/static/{name}", static_file)
    app.router.add_get("/api/session", session_info)
    app.router.add_post("/api/login", login)
    app.router.add_get("/auth/discord", oauth_start)
    app.router.add_get("/auth/callback", oauth_callback)
    app.router.add_post("/api/logout", logout)
    app.router.add_get("/api/bootstrap", bootstrap)
    app.router.add_get("/api/diagnostics", diagnostics)
    app.router.add_post("/api/settings/batch", save_batch)
    app.router.add_post("/api/settings/{key}", save_setting)
    from app.web.portal import register
    register(app)
    return app


async def start_server(bot, *, password_hash: str, bind: str, port: int,
                       discord_client_id: str = "", discord_client_secret: str = "",
                       public_url: str = "") -> web.AppRunner:
    if bind not in ("127.0.0.1", "0.0.0.0") or not 1 <= port <= 65535:
        raise ValueError("Неверный локальный адрес или порт панели.")
    runner = web.AppRunner(create_app(bot, password_hash,
                                  discord_client_id=discord_client_id,
                                  discord_client_secret=discord_client_secret,
                                  public_url=public_url), access_log=None)
    await runner.setup()
    try:
        site = web.TCPSite(runner, host=bind, port=port)
        await site.start()
    except Exception:
        await runner.cleanup()
        raise
    log.info("Локальная панель включена: bind=%s port=%s (Docker host: localhost)", bind, port)
    return runner
