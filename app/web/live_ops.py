"""Staff voice-room and party-finder operations reusing their durable services.

Only Admin/Owner can mutate third-party rooms/parties. Every action checks
Discord state and current guild role; permissions stay enforced server-side.
"""
from __future__ import annotations

import asyncio
import logging

import discord
from aiohttp import web

from app.services import party_finder, voice
from app.services.role_rules import is_approved
from app.web.moderation_portal import identifier, number
from app.web.server import guild_for, json_payload, require_csrf, require_role, state

log = logging.getLogger(__name__)


async def admin(request: web.Request):
    _, actor = await require_role(request, "admin")
    if actor is None:
        raise web.HTTPForbidden(text="Не удалось определить локального оператора Discord.")
    bot = state(request).bot
    guild = guild_for(bot)
    if guild is None:
        raise web.HTTPServiceUnavailable(text="Discord недоступен.")
    return bot, guild, actor


def room_public(room, channel) -> dict:
    return {
        "id": room.id, "channel_id": str(room.channel_id),
        "owner_id": str(room.owner_id), "name": room.name,
        "limit": room.user_limit, "locked": room.locked, "hidden": room.hidden,
        "members": [{"id": str(member.id), "name": member.display_name}
                    for member in channel.members] if channel else [],
        "available": isinstance(channel, discord.VoiceChannel),
    }


def party_public(row) -> dict:
    return {"id": row.id, "game": row.game, "description": row.description,
            "owner_id": str(row.owner_id), "capacity": row.capacity, "members": row.member_count,
            "status": row.status, "starts_at": row.starts_at, "expires_at": row.expires_at,
            "voice_channel_id": str(row.voice_channel_id) if row.voice_channel_id else None}


async def rooms_list(request: web.Request) -> web.Response:
    bot, guild, _ = await admin(request)
    rows = await voice.all_rooms(bot.database, bot.config.guild_id)
    return web.json_response({"items": [room_public(row, guild.get_channel(row.channel_id))
                                         for row in rows]})


async def room_action(request: web.Request) -> web.Response:
    bot, guild, actor = await admin(request)
    require_csrf(request)
    cid = identifier(request.match_info["id"])
    payload = await json_payload(request)
    action = payload.get("action")
    if action not in ("rename", "limit", "lock", "hide", "access", "transfer", "delete"):
        raise web.HTTPBadRequest(text="Недопустимое действие с комнатой.")
    cog = bot.get_cog("Voice")
    if cog is None:
        raise web.HTTPServiceUnavailable(text="Модуль голосовых комнат отключён.")
    lock = cog._room_locks.setdefault(cid, asyncio.Lock())
    async with lock:
        row = await voice.by_channel(bot.database, bot.config.guild_id, cid)
        if row is None:
            raise web.HTTPNotFound(text="Комната больше не существует.")
        channel = guild.get_channel(cid)
        if not isinstance(channel, discord.VoiceChannel):
            raise web.HTTPConflict(text="Голосовой канал больше не существует.")
        me = guild.me
        if me is None or not me.guild_permissions.manage_channels:
            raise web.HTTPForbidden(text="У бота нет Manage Channels.")
        try:
            if action == "rename":
                from app.modules.voice import room_name

                name = room_name(payload.get("name", ""))
                await channel.edit(name=name, reason=f"NewSqad staff {actor.id}: смена имени")
                await voice.rename(bot.database, guild.id, cid, name=name)
            elif action == "limit":
                amount = payload.get("limit")
                if type(amount) is not int or not 0 <= amount <= 99:
                    raise web.HTTPUnprocessableEntity(text="Лимит: 0–99 участников.")
                await channel.edit(user_limit=amount, reason=f"NewSqad staff {actor.id}: лимит")
                await voice.update_state(bot.database, guild.id, cid, user_limit=amount)
            elif action in ("lock", "hide"):
                enabled = payload.get("enabled")
                if type(enabled) is not bool:
                    raise web.HTTPUnprocessableEntity(text="Требуется enabled=true/false.")
                locked = enabled if action == "lock" else row.locked
                hidden = enabled if action == "hide" else row.hidden
                await channel.set_permissions(guild.default_role,
                    view_channel=not hidden, connect=not locked,
                    reason=f"NewSqad staff {actor.id}: доступ комнаты")
                await voice.update_state(bot.database, guild.id, cid,
                    locked=locked, hidden=hidden)
            elif action == "access":
                target_id = identifier(payload.get("user_id"))
                mode = payload.get("mode")
                if mode not in ("allow", "deny", "clear"):
                    raise web.HTTPUnprocessableEntity(text="Недопустимый режим доступа.")
                try:
                    target = await guild.fetch_member(target_id)
                except discord.NotFound as exc:
                    raise web.HTTPBadRequest(text="Участник покинул сервер.") from exc
                if (target.bot or target.id == row.owner_id or
                    target.id == guild.owner_id or target.guild_permissions.administrator):
                    raise web.HTTPForbidden(text="Нельзя ограничивать владельца и администрацию.")
                candidate_id = await bot.database.get_setting(guild.id, "candidate_role_id")
                if not is_approved(target, candidate_id):
                    raise web.HTTPForbidden(text="Доступ только для принятых участников.")
                previous = channel.overwrites_for(target)
                try:
                    if mode == "allow":
                        await channel.set_permissions(target, view_channel=True, connect=True,
                            reason=f"NewSqad staff {actor.id}: разрешён доступ")
                        await voice.set_access(bot.database, row.id, target_id, "allow")
                    elif mode == "deny":
                        await channel.set_permissions(target, view_channel=False, connect=False,
                            reason=f"NewSqad staff {actor.id}: ограничение доступа")
                        await voice.set_access(bot.database, row.id, target_id, "deny")
                    else:
                        await channel.set_permissions(target, overwrite=None,
                            reason=f"NewSqad staff {actor.id}: снято исключение")
                        await voice.clear_access(bot.database, row.id, target_id)
                except Exception:
                    try:
                        await channel.set_permissions(target, overwrite=previous,
                            reason="NewSqad: откат операции доступа")
                    except discord.HTTPException:
                        log.exception("Не удалось откатить права голосовой комнаты %s", cid)
                    raise
            elif action == "transfer":
                recipient_id = identifier(payload.get("user_id"))
                try:
                    recipient = await guild.fetch_member(recipient_id)
                except discord.NotFound as exc:
                    raise web.HTTPBadRequest(text="Новый владелец должен быть на сервере.") from exc
                if recipient not in channel.members:
                    raise web.HTTPUnprocessableEntity(text="Новый владелец должен находиться в комнате.")
                candidate_id = await bot.database.get_setting(guild.id, "candidate_role_id")
                if not is_approved(recipient, candidate_id):
                    raise web.HTTPForbidden(text="Новый владелец ещё кандидат.")
                await cog._transfer_owner(guild, row, recipient)
            else:
                if payload.get("confirm") != "DELETE":
                    raise web.HTTPUnprocessableEntity(text="Подтвердите удаление комнаты.")
                if channel.members:
                    raise web.HTTPConflict(text="Перед удалением все участники должны покинуть комнату.")
                await channel.delete(reason=f"[NSB-VOICE] Администратор {actor.id}: удаление")
                await voice.remove(bot.database, guild.id, cid)
        except ValueError as exc:
            raise web.HTTPUnprocessableEntity(text=str(exc)) from exc
        except discord.HTTPException as exc:
            log.warning("Web: Discord отклонил действие %s с комнатой %s", action, cid)
            raise web.HTTPServiceUnavailable(text="Discord не подтвердил действие с комнатой.") from exc
    return web.json_response({"ok": True, "message": "Голосовая комната обновлена."})


async def parties_list(request: web.Request) -> web.Response:
    bot, _, _ = await admin(request)
    rows = await party_finder.list_active(bot.database, bot.config.guild_id, limit=100)
    return web.json_response({"items": [party_public(row) for row in rows]})


async def party_action(request: web.Request) -> web.Response:
    bot, guild, actor = await admin(request)
    require_csrf(request)
    pid = number(request.match_info["id"])
    payload = await json_payload(request)
    if payload.get("action") != "close" or payload.get("confirm") != "CLOSE":
        raise web.HTTPUnprocessableEntity(text="Подтвердите закрытие объявления.")
    try:
        row = await party_finder.close(bot.database, guild.id, pid, actor.id, admin=True)
    except ValueError as exc:
        raise web.HTTPConflict(text=str(exc)) from exc
    cog = bot.get_cog("PartyFinder")
    if cog:
        await cog.refresh(pid)
        await cog._cleanup_voice(row)
    return web.json_response({"ok": True, "message": "Поиск команды закрыт и ресурсы очищены."})


def register(app: web.Application) -> None:
    app.router.add_get("/api/portal/voice-rooms", rooms_list)
    app.router.add_post("/api/portal/voice-rooms/{id}/action", room_action)
    app.router.add_get("/api/portal/parties", parties_list)
    app.router.add_post("/api/portal/parties/{id}/action", party_action)
