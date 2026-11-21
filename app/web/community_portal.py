"""Admin operational tools: events, giveaway results and community suggestions."""
from __future__ import annotations

import json
import logging
import time

import discord
from aiohttp import web

from app.models import AuditEntry
from app.services import events, suggestions
from app.web.moderation_portal import identifier, number
from app.web.server import guild_for, json_payload, require_csrf, require_role, state

log = logging.getLogger(__name__)


async def admin(request: web.Request):
    _, actor = await require_role(request, "admin")
    return state(request).bot, actor


def require_actor(actor) -> None:
    if actor is None:
        raise web.HTTPForbidden(text="Не удалось определить локального оператора Discord.")


async def record_action(bot, actor_id: int, action: str, entry_id: int) -> None:
    """Only action metadata. Never store private ticket messages or suggestion text."""
    try:
        async with bot.database.session() as session:
            session.add(AuditEntry(
                guild_id=bot.config.guild_id, actor_id=actor_id,
                action=action,
                data_json=json.dumps({"id": entry_id, "source": "web"}, ensure_ascii=False),
                created_at=int(time.time()),
            ))
    except Exception:
        log.exception("Не удалось записать системный аудит %s id=%s", action, entry_id)


def choice(payload, key, values):
    value = payload.get(key)
    if value not in values:
        raise web.HTTPUnprocessableEntity(text="Некорректное значение " + key)
    return value


def message(payload, key, size=500):
    value = payload.get(key)
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= size:
        raise web.HTTPUnprocessableEntity(text=f"Поле {key}: 1–{size} символов.")
    return value.strip()


def positive(payload, key, minimum=0, maximum=1000, default=None):
    value = payload.get(key, default)
    if type(value) is not int or not minimum <= value <= maximum:
        raise web.HTTPUnprocessableEntity(text=f"Поле {key}: число {minimum}–{maximum}.")
    return value


def public_event(row):
    return {"id": row.id, "title": row.title, "description": row.description,
            "status": row.status, "starts_at": row.starts_at, "ends_at": row.ends_at,
            "channel_id": str(row.channel_id), "host_id": str(row.host_id),
            "capacity": row.capacity, "confirmed": row.confirmed_count,
            "waitlisted": row.waitlist_count, "reward_xp": row.reward_xp,
            "reward_coins": row.reward_coins, "message_id":
            str(row.message_id) if row.message_id else None}


def public_giveaway(row):
    return {"id": row.id, "prize": row.prize, "conditions": row.conditions,
            "status": row.status, "ends_at": row.ends_at,
            "channel_id": str(row.channel_id),
            "winner_count": row.winner_count, "entries": row.entry_count,
            "winners": json.loads(row.winners_json), "round": row.draw_round}


async def available_channels(request: web.Request) -> web.Response:
    await admin(request)
    guild = guild_for(state(request).bot)
    if guild is None:
        raise web.HTTPServiceUnavailable(text="Discord недоступен.")
    me = guild.me
    if me is None:
        raise web.HTTPServiceUnavailable(text="Не удалось проверить права бота.")
    items = [{"id": str(ch.id), "name": ch.name}
             for ch in guild.text_channels
             if ch.permissions_for(me).view_channel and ch.permissions_for(me).send_messages]
    return web.json_response({"items": items})


def require_channel(bot, raw):
    cid = identifier(raw)
    guild = guild_for(bot)
    channel = guild.get_channel(cid) if guild else None
    if not isinstance(channel, discord.TextChannel):
        raise web.HTTPUnprocessableEntity(text="Канал не существует.")
    perms = channel.permissions_for(guild.me) if guild.me else None
    if perms is None or not (perms.view_channel and perms.send_messages and perms.embed_links):
        raise web.HTTPForbidden(text="Недостаточно прав Discord на публикацию.")
    return channel


async def events_list(request: web.Request) -> web.Response:
    bot, _ = await admin(request)
    rows = await events.list_events(bot.database, bot.config.guild_id, limit=60)
    return web.json_response({"items": [public_event(row) for row in rows]})


async def event_create(request: web.Request) -> web.Response:
    bot, actor = await admin(request)
    require_csrf(request)
    require_actor(actor)
    data = await json_payload(request)
    if not await bot.database.get_setting(bot.config.guild_id, "events_enabled", True):
        raise web.HTTPConflict(text="Мероприятия отключены в настройках бота.")
    cog = bot.get_cog("Events")
    if cog is None:
        raise web.HTTPServiceUnavailable(text="Модуль мероприятий выключен.")
    channel = require_channel(bot, data.get("channel_id"))
    try:
        start = events.local_to_utc(message(data, "starts", 25), bot.config.timezone)
        row = await events.create_event(
            bot.database, guild_id=bot.config.guild_id,
            creator_id=actor.id, host_id=actor.id, channel_id=channel.id,
            title=message(data, "title", 100), description=message(data, "description", 800),
            starts_at=start, timezone_name=bot.config.timezone,
            duration_minutes=positive(data, "duration", 15, 1440, 120),
            capacity=positive(data, "capacity", 0, 1000, 0),
            waitlist_enabled=data.get("waitlist", True) is True,
            reward_xp=positive(data, "reward_xp", 0, 1000, 0),
            reward_coins=positive(data, "reward_coins", 0, 1000, 0))
    except ValueError as exc:
        raise web.HTTPUnprocessableEntity(text=str(exc)) from exc
    await cog._schedule_event(row)
    try:
        await cog._publish_event(row)
        published = True
    except (discord.HTTPException, RuntimeError):
        log.exception("Мероприятие #%s сохранено, Discord-публикация не подтверждена", row.id)
        published = False
    await record_action(bot, actor.id, "web.events.created", row.id)
    return web.json_response({"ok": True, "item": public_event(row),
                              "message": "Создано и опубликовано." if published
                              else "Создано, но Discord не подтвердил публикацию. Проверьте канал."})


async def event_action(request: web.Request) -> web.Response:
    bot, actor = await admin(request)
    require_csrf(request)
    require_actor(actor)
    eid = number(request.match_info["id"])
    data = await json_payload(request)
    action = choice(data, "action", ("cancel", "attendance"))
    cog = bot.get_cog("Events")
    if cog is None:
        raise web.HTTPServiceUnavailable(text="Модуль мероприятий недоступен.")
    try:
        if action == "cancel":
            if data.get("confirm") != "CANCEL":
                raise web.HTTPUnprocessableEntity(text="Подтвердите отмену.")
            reason = message(data, "reason", 400)
            if not await events.cancel_event(bot.database, bot.config.guild_id, eid, reason=reason):
                raise web.HTTPConflict(text="Мероприятие завершено или отсутствует.")
            await cog._refresh_event(eid)
            await record_action(bot, actor.id, "web.events.cancelled", eid)
            return web.json_response({"ok": True, "message": "Мероприятие отменено."})
        uid = identifier(data.get("user_id"))
        guild = guild_for(bot)
        if guild is None:
            raise web.HTTPServiceUnavailable()
        try:
            await guild.fetch_member(uid)
        except discord.NotFound as exc:
            raise web.HTTPUnprocessableEntity(text="Участник покинул сервер.") from exc
        xp, coins = await events.confirm_attendance(
            bot.database, guild_id=bot.config.guild_id, event_id=eid,
            user_id=uid, actor_id=actor.id, timezone_name=bot.config.timezone)
        await record_action(bot, actor.id, "web.events.attendance", eid)
        return web.json_response({"ok": True,
                                  "message": f"Участие подтверждено: {xp} XP, {coins} NS Coins."})
    except ValueError as exc:
        raise web.HTTPUnprocessableEntity(text=str(exc)) from exc


async def event_detail(request: web.Request) -> web.Response:
    bot, _ = await admin(request)
    eid = number(request.match_info["id"])
    row = await events.get_event(bot.database, bot.config.guild_id, eid)
    if row is None:
        raise web.HTTPNotFound(text="Мероприятие не найдено.")
    members = await events.event_signups(bot.database, eid)
    return web.json_response({"event": public_event(row), "members": [
        {"id": str(v.user_id), "status": v.status,
         "attended": bool(v.attended_at)} for v in members]})


async def giveaways_list(request: web.Request) -> web.Response:
    bot, _ = await admin(request)
    rows = await events.list_giveaways(bot.database, bot.config.guild_id, limit=60)
    return web.json_response({"items": [public_giveaway(row) for row in rows]})


async def giveaway_create(request: web.Request) -> web.Response:
    bot, actor = await admin(request)
    require_csrf(request)
    require_actor(actor)
    data = await json_payload(request)
    if not await bot.database.get_setting(bot.config.guild_id, "giveaways_enabled", True):
        raise web.HTTPConflict(text="Розыгрыши отключены.")
    cog = bot.get_cog("Events")
    if cog is None:
        raise web.HTTPServiceUnavailable(text="Модуль розыгрышей выключен.")
    channel = require_channel(bot, data.get("channel_id"))
    try:
        end = events.local_to_utc(message(data, "ends", 25), bot.config.timezone)
        row = await events.create_giveaway(
            bot.database, guild_id=bot.config.guild_id, creator_id=actor.id,
            channel_id=channel.id, prize=message(data, "prize", 200),
            conditions=message(data, "conditions", 500),
            ends_at=end, winner_count=positive(data, "winners", 1, 20, 1),
            min_level=positive(data, "min_level", 0, 1000, 0))
    except ValueError as exc:
        raise web.HTTPUnprocessableEntity(text=str(exc)) from exc
    await cog._schedule_giveaway(row)
    try:
        await cog._publish_giveaway(row)
        published = True
    except (discord.HTTPException, RuntimeError):
        log.exception("Розыгрыш #%s не опубликован", row.id)
        published = False
    await record_action(bot, actor.id, "web.giveaways.created", row.id)
    return web.json_response({"ok": True, "item": public_giveaway(row),
                              "message": "Розыгрыш открыт." if published
                              else "Розыгрыш сохранён, Discord-публикация не подтверждена."})


async def giveaway_action(request: web.Request) -> web.Response:
    bot, actor = await admin(request)
    require_csrf(request)
    require_actor(actor)
    gid = number(request.match_info["id"])
    data = await json_payload(request)
    action = choice(data, "action", ("draw", "reroll"))
    cog = bot.get_cog("Events")
    if cog is None:
        raise web.HTTPServiceUnavailable(text="Модуль мероприятий не загружен.")
    if data.get("confirm") != "DRAW":
        raise web.HTTPUnprocessableEntity(text="Подтвердите выбор победителей.")
    try:
        if action == "draw":
            winners = await cog._draw(gid, actor_id=actor.id,
                                      reason="Досрочное завершение через сайт", force=True)
        else:
            reason = message(data, "reason", 400)
            winners = await events.reroll_giveaway(
                bot.database, guild_id=bot.config.guild_id, giveaway_id=gid,
                actor_id=actor.id, reason=reason)
            await cog._refresh_giveaway(gid)
    except ValueError as exc:
        raise web.HTTPUnprocessableEntity(text=str(exc)) from exc
    await record_action(bot, actor.id,
                        "web.giveaways.draw" if action == "draw" else "web.giveaways.reroll", gid)
    return web.json_response({"ok": True, "winners": [str(uid) for uid in winners]})


async def suggestions_list(request: web.Request) -> web.Response:
    bot, _ = await admin(request)
    status = request.query.get("status", "all")
    category = request.query.get("category", "all")
    if status not in ("all", "new", "under_review", "accepted", "rejected", "implemented"):
        raise web.HTTPBadRequest(text="Неверный статус.")
    if category not in ("all", "general", "games", "events", "server", "other"):
        raise web.HTTPBadRequest(text="Неверная категория.")
    rows = await suggestions.list_items(
        bot.database, bot.config.guild_id, status=None if status == "all" else status,
        category=None if category == "all" else category, limit=80)
    return web.json_response({"items": [
        {"id": item.id, "topic": item.topic, "description": item.description,
         "category": item.category, "status": item.status, "author_id": str(item.author_id),
         "votes_for": item.votes_for, "votes_against": item.votes_against,
         "reason": item.status_reason, "created_at": item.created_at}
        for item in rows]})


async def suggestion_action(request: web.Request) -> web.Response:
    bot, actor = await admin(request)
    require_csrf(request)
    require_actor(actor)
    sid = number(request.match_info["id"])
    data = await json_payload(request)
    status = choice(data, "status", ("new", "under_review", "accepted", "rejected", "implemented"))
    try:
        row = await suggestions.change_status(
            bot.database, guild_id=bot.config.guild_id, suggestion_id=sid,
            actor_id=actor.id, status=status, reason=message(data, "reason", 500))
    except ValueError as exc:
        raise web.HTTPUnprocessableEntity(text=str(exc)) from exc
    cog = bot.get_cog("Suggestions")
    if cog:
        await cog._refresh(row.id)
    await record_action(bot, actor.id, "web.suggestions.status_changed", row.id)
    return web.json_response({"ok": True, "message": "Статус идеи обновлён; автор будет уведомлён."})


def register(app: web.Application) -> None:
    app.router.add_get("/api/portal/channels", available_channels)
    app.router.add_get("/api/portal/events", events_list)
    app.router.add_post("/api/portal/events", event_create)
    app.router.add_get("/api/portal/events/{id}", event_detail)
    app.router.add_post("/api/portal/events/{id}/action", event_action)
    app.router.add_get("/api/portal/giveaways", giveaways_list)
    app.router.add_post("/api/portal/giveaways", giveaway_create)
    app.router.add_post("/api/portal/giveaways/{id}/action", giveaway_action)
    app.router.add_get("/api/portal/suggestions", suggestions_list)
    app.router.add_post("/api/portal/suggestions/{id}/action", suggestion_action)
