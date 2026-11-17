"""Live metrics, aggregated support quality and role-aware notification API."""
from __future__ import annotations

import asyncio
import time

from aiohttp import web
from sqlalchemy import func, select

from app.models import Application, AuditEntry, Ticket
from app.services import support_quality, web_notifications
from app.web.oauth import site_role
from app.web.server import guild_for, require_auth, require_csrf, require_role, state

scan_lock = asyncio.Lock()
last_scan = {}


async def dashboard(request: web.Request) -> web.Response:
    role, member = await require_role(request)
    bot = state(request).bot
    guild = guild_for(bot)
    gid = bot.config.guild_id
    async with bot.database.sessions() as session:
        open_tickets = await session.scalar(select(func.count(Ticket.id)).where(
            Ticket.guild_id == gid, Ticket.status == "open"))
        pending = await session.scalar(select(func.count(Application.id)).where(
            Application.guild_id == gid,
            Application.status.in_(("pending", "in_review", "needs_info",
                                    "action_failed", "approving", "rejecting"))))
        recent = []
        if role in ("owner", "admin"):
            logs = (await session.scalars(select(AuditEntry).where(
                AuditEntry.guild_id == gid, AuditEntry.actor_id > 0)
                .order_by(AuditEntry.created_at.desc(), AuditEntry.id.desc())
                .limit(8))).all()
            recent = [
                {"actor_id": str(row.actor_id), "action": row.action, "at": row.created_at}
                for row in logs]
    people = []
    presence_available = bool(getattr(getattr(bot, "intents", None), "presences", False))
    if guild is not None:
        for employee in guild.members:
            staff_role = site_role(employee, guild)
            if not staff_role:
                continue
            raw_status = str(employee.status) if presence_available else "unknown"
            people.append({
                "id": str(employee.id), "name": employee.display_name,
                "avatar_url": str(employee.display_avatar.url),
                "role": staff_role,
                "status": raw_status,
            })
    order = {"online": 0, "idle": 1, "dnd": 2, "offline": 3, "unknown": 4}
    people.sort(key=lambda p: (order.get(p["status"], 5), p["name"].casefold()))
    scheduler = getattr(bot, "scheduler", None)
    event_bus = getattr(bot, "events", None)
    return web.json_response({
        "generated_at": int(time.time()), "role": role,
        "open_tickets": int(open_tickets or 0),
        "pending_applications": int(pending or 0),
        "discord_connected": bool(bot.is_ready() and guild),
        "sqlite_connected": bool(await bot.database.ping()),
        "scheduler_running": bool(scheduler and scheduler.running),
        "events_running": bool(event_bus and event_bus.running),
        "module_errors": [k for k, value in getattr(bot, "module_states", {}).items()
                          if value == "error"] if role in ("owner", "admin") else [],
        "presence_available": presence_available,
        "online_staff": sum(1 for p in people if p["status"] in ("online", "idle", "dnd"))
            if presence_available else None,
        "staff": people[:60],
        "recent_actions": recent,
    })


async def quality(request: web.Request) -> web.Response:
    await require_role(request, "admin")
    bot = state(request).bot
    value = request.query.get("days", "30")
    if value not in ("7", "30", "90"):
        raise web.HTTPBadRequest(text="Период: 7, 30 или 90 дней.")
    data = await support_quality.summary(bot.database, bot.config.guild_id, days=int(value))
    guild = guild_for(bot)
    for row in data["staff"]:
        person = guild.get_member(int(row["user_id"])) if guild else None
        row["name"] = person.display_name if person else "Сотрудник"
    return web.json_response(data)


async def notices(request: web.Request) -> web.Response:
    role, member = await require_role(request)
    if member is None:
        return web.json_response({"unread": 0, "items": [], "requires_discord": True})
    bot = state(request).bot
    now = int(time.time())
    key = id(bot)
    if now - last_scan.get(key, 0) >= 30:
        async with scan_lock:
            if now - last_scan.get(key, 0) >= 30:
                await web_notifications.sync_sources(bot.database, bot.config.guild_id, now=now)
                last_scan[key] = now
    value = await web_notifications.inbox(bot.database, bot.config.guild_id, member.id, role)
    return web.json_response(value)


async def notice_read(request: web.Request) -> web.Response:
    role, member = await require_role(request)
    require_csrf(request)
    if member is None:
        raise web.HTTPForbidden(text="Для уведомлений нужен вход через Discord.")
    identifier = request.match_info["id"]
    if identifier != "all":
        if not identifier.isascii() or not identifier.isdecimal() or len(identifier) > 12:
            raise web.HTTPBadRequest(text="Некорректный номер уведомления.")
        notice_id = int(identifier)
    else:
        notice_id = None
    bot = state(request).bot
    count = await web_notifications.mark_read(
        bot.database, bot.config.guild_id, member.id, role, notice_id=notice_id)
    if notice_id is not None and count == 0:
        raise web.HTTPNotFound(text="Уведомление не найдено или недоступно.")
    return web.json_response({"ok": True, "updated": count})


def register(app: web.Application) -> None:
    app.router.add_get("/api/portal/dashboard", dashboard)
    app.router.add_get("/api/portal/support-quality", quality)
    app.router.add_get("/api/portal/notifications", notices)
    app.router.add_post("/api/portal/notifications/{id}/read", notice_read)
