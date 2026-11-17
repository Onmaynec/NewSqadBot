"""Role-guarded staff console. Private ticket messages come from Discord on demand.

No message text is persisted, exported, indexed, written to logs or analytics.
Every endpoint revalidates the viewer's CURRENT guild roles through Discord REST.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import discord
from aiohttp import web
from sqlalchemy import func, select

from app.models import (
    Application, ApplicationEvent, AuditEntry, ModerationAction, ModerationCase,
    StatsDay, Ticket, TicketEvent,
)
from app.services import applications as membership_store
from app.services import moderation_cases, statistics as stats, tickets as ticket_store
from app.web.oauth import ROLE_IDS, site_role
from app.web.server import guild_for, json_payload, require_auth, require_csrf, require_role, state

log = logging.getLogger(__name__)


def snowflake(value: str) -> int:
    if not value.isdecimal() or not 1 <= len(value) <= 20:
        raise web.HTTPBadRequest(text="Недопустимый идентификатор.")
    result = int(value)
    if not 0 < result < 2**64:
        raise web.HTTPBadRequest(text="Недопустимый идентификатор.")
    return result


def number(value: str) -> int:
    if not value.isdecimal() or not 1 <= len(value) <= 10:
        raise web.HTTPBadRequest(text="Недопустимый номер.")
    return int(value)


def user_card(member: discord.Member) -> dict:
    return {"id": str(member.id), "name": member.display_name,
            "avatar_url": str(member.display_avatar.url),
            "joined_at": int(member.joined_at.timestamp()) if member.joined_at else None,
            "roles": [str(role.id) for role in member.roles if not role.is_default()]}


def public_ticket(row: Ticket) -> dict:
    return {"id": row.id, "user_id": str(row.user_id),
            "assignee_id": str(row.assignee_id) if row.assignee_id else None,
            "subject": row.subject, "status": row.status, "opened_at": row.opened_at,
            "updated_at": row.updated_at, "first_response_at": row.first_response_at,
            "category_id": row.category_id}


def public_application(row: Application) -> dict:
    return {"id": row.id, "user_id": str(row.user_id), "username": row.username,
            "name": row.name, "age": row.age, "gender": row.gender, "inviter": row.inviter,
            "status": row.status, "created_at": row.created_at,
            "reviewer_id": str(row.reviewer_id) if row.reviewer_id else None,
            "decision": row.decision, "decision_reason": row.decision_reason,
            "error": row.last_error}


async def overview(request: web.Request) -> web.Response:
    role, _ = await require_role(request)
    bot = state(request).bot
    gid = bot.config.guild_id
    db = bot.database
    async with db.sessions() as session:
        tickets = await session.scalar(select(func.count(Ticket.id)).where(
            Ticket.guild_id == gid, Ticket.status == "open"))
        pending = await session.scalar(select(func.count(Application.id)).where(
            Application.guild_id == gid,
            Application.status.in_(("pending", "in_review", "needs_info", "action_failed"))))
    return web.json_response({
        "role": role, "open_tickets": int(tickets or 0),
        "pending_applications": int(pending or 0),
        "guild_name": guild_for(bot).name if guild_for(bot) else "NewSqad",
    })


async def applications_list(request: web.Request) -> web.Response:
    await require_role(request)
    bot = state(request).bot
    status = request.query.get("status", "open")
    allowed = {"open", "approved", "rejected", "all"}
    if status not in allowed:
        raise web.HTTPBadRequest(text="Недопустимый статус.")
    async with bot.database.sessions() as session:
        q = select(Application).where(Application.guild_id == bot.config.guild_id)
        if status == "open":
            q = q.where(Application.status.in_(("pending", "in_review", "needs_info", "action_failed",
                                                 "approving", "rejecting")))
        elif status != "all":
            q = q.where(Application.status == status)
        values = (await session.scalars(q.order_by(Application.id.desc()).limit(100))).all()
    return web.json_response({"items": [public_application(row) for row in values]})


async def application_detail(request: web.Request) -> web.Response:
    await require_role(request)
    bot = state(request).bot
    aid = number(request.match_info["id"])
    row = await membership_store.get_application(bot.database, aid)
    if not row or row.guild_id != bot.config.guild_id:
        raise web.HTTPNotFound(text="Заявка не найдена.")
    changes = await membership_store.timeline(bot.database, bot.config.guild_id, aid)
    return web.json_response({"application": public_application(row), "history": [
        {"actor_id": str(evt.actor_id), "previous": evt.old_status,
         "status": evt.new_status, "reason": evt.reason, "at": evt.created_at}
        for evt in changes]})


async def application_action(request: web.Request) -> web.Response:
    await require_role(request)
    require_csrf(request)
    bot = state(request).bot
    aid = number(request.match_info["id"])
    data = await json_payload(request)
    action = data.get("action")
    row = await membership_store.get_application(bot.database, aid)
    if not row or row.guild_id != bot.config.guild_id:
        raise web.HTTPNotFound(text="Заявка не найдена.")
    actor = require_auth(request).user_id
    if not actor:
        raise web.HTTPForbidden(text="Требуется авторизация Discord для действий.")
    cog = bot.get_cog("Membership")
    if cog is None:
        raise web.HTTPServiceUnavailable(text="Модуль заявок сейчас не загружен.")
    if action in ("approve", "reject"):
        reason = str(data.get("reason", "")).strip()
        if action == "reject" and not 3 <= len(reason) <= 400:
            raise web.HTTPUnprocessableEntity(text="Причина отказа: 3–400 символов.")
        if action == "approve":
            reason = "Одобрено через сайт"
        stage = "approving" if action == "approve" else "rejecting"
        ok = await membership_store.transition(
            bot.database, aid, expected=membership_store.REVIEWABLE, target=stage,
            actor_id=actor, reason=reason,
            decision="approve" if action == "approve" else "reject")
        if not ok:
            return web.json_response({"error": "Другое решение уже выполняется."}, status=409)
        result = await cog.execute(aid)
        await cog.refresh(aid)
        return web.json_response({"ok": True, "message": result})
    if action == "retry":
        if row.status not in ("action_failed", "approving", "rejecting"):
            return web.json_response({"error": "Повторять сейчас нечего."}, status=409)
        result = await cog.execute(aid)
        await cog.refresh(aid)
        return web.json_response({"ok": True, "message": result})
    if action == "claim":
        ok = await membership_store.assign_reviewer(bot.database, bot.config.guild_id, aid, actor, actor)
        if ok:
            await cog.refresh(aid)
        return web.json_response({"ok": ok, "message": "Ответственный назначен." if ok else "Заявка уже закрыта."},
                                 status=200 if ok else 409)
    if action == "request_info":
        reason = str(data.get("reason", "")).strip()
        if not 5 <= len(reason) <= 500:
            raise web.HTTPUnprocessableEntity(text="Укажите вопрос от 5 до 500 символов.")
        ok = await membership_store.transition(
            bot.database, aid, expected=("pending", "in_review"), target="needs_info",
            actor_id=actor, reason="Уточнение через сайт: " + reason)
        if not ok:
            return web.json_response({"error": "Заявка уже ожидает ответа или закрыта."}, status=409)
        await cog.refresh(aid)
        try:
            user = bot.get_user(row.user_id) or await bot.fetch_user(row.user_id)
            await user.send("NewSqad: заявка #" + str(aid) + " — уточните: " + reason,
                            allowed_mentions=discord.AllowedMentions.none())
            result = "Вопрос отправлен кандидату."
        except discord.HTTPException:
            result = "Вопрос сохранён, но ЛС кандидату отправить не удалось."
        return web.json_response({"ok": True, "message": result})
    raise web.HTTPBadRequest(text="Недопустимое действие.")


async def ticket_list(request: web.Request) -> web.Response:
    await require_role(request)
    bot = state(request).bot
    rows = await ticket_store.list_tickets(bot.database, bot.config.guild_id,
                                           statuses=("open", "creating", "closing"), limit=100)
    return web.json_response({"items": [public_ticket(row) for row in rows]})


async def _ticket_channel(bot, row: Ticket):
    if row.status != "open" or row.channel_id is None:
        raise web.HTTPConflict(text="Обращение не открыто.")
    guild = guild_for(bot)
    if guild is None:
        raise web.HTTPServiceUnavailable(text="Discord недоступен.")
    channel = guild.get_channel(row.channel_id)
    if not isinstance(channel, discord.TextChannel):
        try:
            channel = await guild.fetch_channel(row.channel_id)
        except discord.HTTPException as exc:
            raise web.HTTPServiceUnavailable(text="Канал тикета недоступен.") from exc
    if not isinstance(channel, discord.TextChannel) or channel.guild.id != guild.id:
        raise web.HTTPForbidden(text="Это не приватный тикет NewSqad.")
    return channel


async def ticket_detail(request: web.Request) -> web.Response:
    await require_role(request)
    bot = state(request).bot
    tid = number(request.match_info["id"])
    row = await ticket_store.get(bot.database, bot.config.guild_id, tid)
    if row is None:
        raise web.HTTPNotFound(text="Обращение не найдено.")
    channel = await _ticket_channel(bot, row)
    try:
        # ВНИМАНИЕ: история только в HTTP-ответе. Не кешировать и не писать в SQLite.
        messages = [m async for m in channel.history(limit=50, oldest_first=False)]
    except discord.HTTPException as exc:
        log.warning("Discord не предоставил историю тикета #%s", tid)
        raise web.HTTPServiceUnavailable(text="Не удалось прочитать переписку.") from exc
    return web.json_response({
        "ticket": public_ticket(row),
        "messages": [
            {"id": str(m.id), "author": m.author.display_name,
             "avatar_url": str(m.author.display_avatar.url),
             "bot": m.author.bot,
             "text": m.content, "at": int(m.created_at.timestamp()),
             "attachments": [{"name": att.filename, "url": att.url}
                             for att in m.attachments[:5]]}
            for m in reversed(messages)],
    })


async def ticket_action(request: web.Request) -> web.Response:
    role, member = await require_role(request)
    require_csrf(request)
    bot = state(request).bot
    tid = number(request.match_info["id"])
    row = await ticket_store.get(bot.database, bot.config.guild_id, tid)
    if row is None or row.status != "open":
        raise web.HTTPNotFound(text="Открытый тикет не найден.")
    data = await json_payload(request)
    action = data.get("action")
    actor = member.id if member else None
    if actor is None:
        raise web.HTTPForbidden(text="Необходим вход через Discord.")
    if action == "reply":
        value = data.get("text")
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= 1500:
            raise web.HTTPUnprocessableEntity(text="Ответ: от 1 до 1500 символов.")
        channel = await _ticket_channel(bot, row)
        # Send as BOT identity with a clear staff attribution, no pings, no DB message storage.
        safe = discord.utils.escape_mentions(discord.utils.escape_markdown(member.display_name))
        try:
            await channel.send(f"**Ответ от {safe}:**\n{value.strip()}",
                               allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException as exc:
            log.warning("Не удалось отправить ответ в тикет #%s", tid)
            raise web.HTTPServiceUnavailable(text="Discord не подтвердил отправку.") from exc
        return web.json_response({"ok": True, "message": "Ответ отправлен."})
    if action in ("claim", "transfer"):
        target = actor
        if action == "transfer":
            raw = data.get("user_id")
            if not isinstance(raw, str):
                raise web.HTTPBadRequest(text="Не указан сотрудник.")
            target = snowflake(raw)
            guild = guild_for(bot)
            try:
                new_staff = await guild.fetch_member(target)
            except discord.HTTPException as exc:
                raise web.HTTPBadRequest(text="Сотрудник не найден на сервере.") from exc
            if site_role(new_staff, guild) is None:
                raise web.HTTPForbidden(text="У сотрудника нет доступа к поддержке.")
        success = await ticket_store.claim(bot.database, bot.config.guild_id, tid, target,
                                           transfer=action == "transfer")
        if not success:
            return web.json_response({"error": "Обращение уже взято или закрывается."}, status=409)
        cog = bot.get_cog("Tickets")
        if cog is not None:
            await cog.refresh(tid)
        return web.json_response({"ok": True, "message": "Ответственный обновлён."})
    if action == "close":
        if data.get("confirm") != "DELETE":
            raise web.HTTPUnprocessableEntity(text="Подтвердите безвозвратное удаление тикета.")
        ok = await ticket_store.start_close(bot.database, bot.config.guild_id, tid,
                                            actor, "Закрыто через сайт")
        if not ok:
            return web.json_response({"error": "Обращение уже закрывается."}, status=409)
        cog = bot.get_cog("Tickets")
        if cog is None:
            raise web.HTTPServiceUnavailable(text="Модуль тикетов не загружен. Закрытие восстановится после запуска.")
        result = await cog._delete_ticket(row)
        return web.json_response({"ok": True, "message": result})
    raise web.HTTPBadRequest(text="Неизвестная операция.")


async def members_list(request: web.Request) -> web.Response:
    await require_role(request, "moder")
    guild = guild_for(state(request).bot)
    if guild is None:
        raise web.HTTPServiceUnavailable()
    phrase = request.query.get("q", "").casefold().strip()[:60]
    matched = [member for member in guild.members if not member.bot and (
        not phrase or phrase in member.display_name.casefold() or phrase in str(member.id))]
    return web.json_response({"items": [user_card(m) for m in matched[:100]],
                              "cached_members": len(guild.members),
                              "server_member_count": guild.member_count})


async def staff_list(request: web.Request) -> web.Response:
    await require_role(request)
    guild = guild_for(state(request).bot)
    if guild is None:
        raise web.HTTPServiceUnavailable()
    # Presence intent disabled by default; do not claim "offline" without data.
    available = bool(state(request).bot.intents.presences)
    people = []
    for member in guild.members:
        role = site_role(member, guild)
        if role:
            status = str(member.status) if available else "unknown"
            people.append({**user_card(member), "role": role, "status": status})
    return web.json_response({"items": people, "presence_available": available})


async def server_stats(request: web.Request) -> web.Response:
    await require_role(request, "admin")
    bot = state(request).bot
    period = request.query.get("period", "7d")
    if period not in ("7d", "30d", "90d"):
        raise web.HTTPBadRequest(text="Недопустимый период.")
    bound = stats.period_bounds(period, timezone=bot.config.timezone)
    guild = guild_for(bot)
    if guild is None:
        raise web.HTTPServiceUnavailable()
    candidate_role_id = await bot.database.get_setting(bot.config.guild_id, "candidate_role_id")
    member_role_id = await bot.database.get_setting(bot.config.guild_id, "member_role_id")
    totals = await stats.server_totals(
        bot.database, guild_id=bot.config.guild_id, period=bound,
        members=guild.members, member_count=guild.member_count or len(guild.members),
        member_role_id=member_role_id, candidate_role_id=candidate_role_id,
        tracking_started_at=0)
    series = await stats.activity_series(bot.database, bot.config.guild_id, bound)
    return web.json_response({"period": period, "totals": asdict(totals),
                              "series": [{"date": x[0], "messages": x[1],
                                          "voice": x[2], "joins": x[3], "leaves": x[4]}
                                         for x in series]})


async def tops(request: web.Request) -> web.Response:
    await require_role(request, "admin")
    metric = request.query.get("metric", "xp")
    period = request.query.get("period", "7d")
    if metric not in ("xp", "messages", "voice") or period not in ("7d", "30d", "90d"):
        raise web.HTTPBadRequest(text="Недопустимый показатель.")
    bot = state(request).bot
    guild = guild_for(bot)
    if guild is None:
        raise web.HTTPServiceUnavailable()
    bound = stats.period_bounds(period, timezone=bot.config.timezone)
    candidate = await bot.database.get_setting(bot.config.guild_id, "candidate_role_id")
    eligible = await stats.approved_ids(bot.database, bot.config.guild_id, guild.members, candidate)
    rows = await stats.leaderboard(bot.database, guild_id=bot.config.guild_id, period=bound,
                                   metric=metric, active_user_ids=eligible, limit=20)
    return web.json_response({"metric": metric, "items": [
        {"user_id": str(uid), "name": guild.get_member(uid).display_name
         if guild.get_member(uid) else str(uid), "value": value}
        for uid, value in rows]})


async def audit(request: web.Request) -> web.Response:
    await require_role(request, "admin")
    bot = state(request).bot
    async with bot.database.sessions() as session:
        entries = (await session.scalars(select(AuditEntry).where(
            AuditEntry.guild_id == bot.config.guild_id
        ).order_by(AuditEntry.id.desc()).limit(100))).all()
    return web.json_response({"items": [
        {"id": r.id, "actor_id": str(r.actor_id), "action": r.action,
         "details": json.loads(r.data_json), "at": r.created_at}
        for r in entries]})


async def moderation_list(request: web.Request) -> web.Response:
    await require_role(request, "moder")
    bot = state(request).bot
    rows = await moderation_cases.find(bot.database, bot.config.guild_id, limit=50)
    return web.json_response({"items": [
        {"id": r.id, "user_id": str(r.user_id), "moderator_id": str(r.moderator_id),
         "kind": r.kind, "status": r.status, "reason": r.reason,
         "expires_at": r.expires_at, "created_at": r.created_at}
        for r in rows]})


async def admin_activity(request: web.Request) -> web.Response:
    await require_role(request, "admin")
    bot = state(request).bot
    gid = bot.config.guild_id
    since = int(time.time()) - 30 * 86400
    async with bot.database.sessions() as session:
        approvals = (await session.execute(select(
            ApplicationEvent.actor_id, func.count()).join(
            Application, Application.id == ApplicationEvent.application_id).where(
            Application.guild_id == gid,
            ApplicationEvent.new_status.in_(("approved", "rejected")),
            ApplicationEvent.created_at >= since).group_by(ApplicationEvent.actor_id))).all()
        changes = (await session.execute(select(
            AuditEntry.actor_id, func.count()).where(
            AuditEntry.guild_id == gid, AuditEntry.created_at >= since,
            AuditEntry.actor_id > 0).group_by(AuditEntry.actor_id))).all()
        cases = (await session.execute(select(
            ModerationCase.moderator_id, func.count()).where(
            ModerationCase.guild_id == gid, ModerationCase.created_at >= since
        ).group_by(ModerationCase.moderator_id))).all()
        active_tickets = (await session.execute(select(
            Ticket.assignee_id, func.count()).where(
            Ticket.guild_id == gid, Ticket.status == "open",
            Ticket.assignee_id.is_not(None)).group_by(Ticket.assignee_id))).all()
    data = {}
    for attr, rows in (("applications", approvals), ("audit_actions", changes),
                       ("moderation_cases", cases), ("active_tickets", active_tickets)):
        for uid, count in rows:
            if uid:
                data.setdefault(str(uid), {"user_id": str(uid), "applications": 0,
                            "audit_actions": 0, "moderation_cases": 0, "active_tickets": 0})[attr] = count
    guild = guild_for(bot)
    for uid, values in data.items():
        member = guild.get_member(int(uid)) if guild else None
        values["name"] = member.display_name if member else uid
    return web.json_response({"period": "30d", "items": list(data.values())})


def register(app: web.Application) -> None:
    app.router.add_get("/api/portal/overview", overview)
    app.router.add_get("/api/portal/applications", applications_list)
    app.router.add_get("/api/portal/applications/{id}", application_detail)
    app.router.add_post("/api/portal/applications/{id}/action", application_action)
    app.router.add_get("/api/portal/tickets", ticket_list)
    app.router.add_get("/api/portal/tickets/{id}", ticket_detail)
    app.router.add_post("/api/portal/tickets/{id}/action", ticket_action)
    app.router.add_get("/api/portal/members", members_list)
    app.router.add_get("/api/portal/staff", staff_list)
    app.router.add_get("/api/portal/stats", server_stats)
    app.router.add_get("/api/portal/tops", tops)
    app.router.add_get("/api/portal/audit", audit)
    app.router.add_get("/api/portal/moderation", moderation_list)
    app.router.add_get("/api/portal/activity", admin_activity)
