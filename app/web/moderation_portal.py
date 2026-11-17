"""Moderation HTTP entrypoints backed by the Discord Moderation cog."""
from __future__ import annotations

from aiohttp import web

from app.services import moderation_cases
from app.web.server import guild_for, json_payload, require_csrf, require_role, state


def identifier(value: str) -> int:
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal() or not 1 <= len(value) <= 20:
        raise web.HTTPBadRequest(text="Некорректный Discord ID.")
    number = int(value)
    if not 0 < number < 2**64:
        raise web.HTTPBadRequest(text="Некорректный Discord ID.")
    return number


def number(value: str) -> int:
    if not value.isascii() or not value.isdecimal() or not 1 <= len(value) <= 10:
        raise web.HTTPBadRequest(text="Некорректный номер дела.")
    return int(value)


def public_case(row) -> dict:
    return {"id": row.id, "user_id": str(row.user_id), "moderator_id": str(row.moderator_id),
            "kind": row.kind, "status": row.status, "reason": row.reason,
            "expires_at": row.expires_at, "created_at": row.created_at,
            "completed_at": row.completed_at, "closed_by": str(row.closed_by) if row.closed_by else None,
            "dm_status": row.dm_status, "error": row.error}


async def cases_list(request: web.Request) -> web.Response:
    await require_role(request, "moder")
    bot = state(request).bot
    kind = request.query.get("kind", "all")
    status = request.query.get("status", "all")
    user_id = request.query.get("user", "").strip()
    case_number = request.query.get("number", "").strip()
    if kind not in ("all", "ban", "mute", "kick", "warn") or status not in ("all", "active", "closed"):
        raise web.HTTPBadRequest(text="Неизвестный фильтр модерации.")
    rows = await moderation_cases.find(
        bot.database, bot.config.guild_id,
        user_id=identifier(user_id) if user_id else None,
        number=number(case_number) if case_number else None,
        kind=None if kind == "all" else kind,
        status=None if status == "all" else status, limit=50)
    return web.json_response({"items": [public_case(row) for row in rows]})


async def case_detail(request: web.Request) -> web.Response:
    await require_role(request, "moder")
    bot = state(request).bot
    case_id = number(request.match_info["id"])
    rows = await moderation_cases.find(bot.database, bot.config.guild_id, number=case_id)
    if not rows:
        raise web.HTTPNotFound(text="Дело не найдено.")
    timeline = await moderation_cases.actions(bot.database, bot.config.guild_id, case_id, limit=40)
    return web.json_response({"case": public_case(rows[0]),
                              "history": [{"action": step.action, "actor_id": str(step.actor_id),
                                           "details": step.details, "at": step.created_at}
                                          for step in timeline]})


async def case_create(request: web.Request) -> web.Response:
    _, actor = await require_role(request, "moder")
    require_csrf(request)
    payload = await json_payload(request)
    kind, uid = payload.get("kind"), payload.get("user_id")
    reason, duration = payload.get("reason"), payload.get("duration", "")
    if (kind not in ("ban", "mute", "kick", "warn")
            or not isinstance(reason, str) or not 3 <= len(reason.strip()) <= 400
            or not isinstance(duration, str) or len(duration) > 25):
        raise web.HTTPUnprocessableEntity(text="Проверьте тип наказания, срок и причину.")
    bot = state(request).bot
    guild = guild_for(bot)
    cog = bot.get_cog("Moderation")
    if guild is None or cog is None:
        raise web.HTTPServiceUnavailable(text="Модерация или Discord недоступны.")
    result = await cog.create_from_web(guild, actor, identifier(uid), kind, reason, duration)
    success = result.startswith("Операция ")
    return web.json_response({"ok": success, "message" if success else "error": result},
                             status=200 if success else 409)


async def case_action(request: web.Request) -> web.Response:
    role, actor = await require_role(request, "moder")
    require_csrf(request)
    payload = await json_payload(request)
    action, value = payload.get("action"), payload.get("value")
    if action not in ("reason", "duration", "revoke") or not isinstance(value, str) or len(value) > 400:
        raise web.HTTPUnprocessableEntity(text="Недопустимое изменение наказания.")
    if action == "revoke" and payload.get("confirm") != "REVOKE":
        raise web.HTTPUnprocessableEntity(text="Необходимо подтвердить отмену.")
    bot = state(request).bot
    cog = bot.get_cog("Moderation")
    guild = guild_for(bot)
    if cog is None or guild is None:
        raise web.HTTPServiceUnavailable(text="Модерация или Discord недоступны.")
    case_id = number(request.match_info["id"])
    privileged = (actor.id == guild.owner_id or actor.guild_permissions.administrator) and role in (
        "owner", "admin")
    result = await cog.change_case(
        guild, actor, case_id, action=action, value=value, privileged=bool(privileged))
    success = result.startswith((f"Дело #{case_id:05d} отменено.",
                                 f"Причина дела #{case_id:05d} изменена.",
                                 f"Срок дела #{case_id:05d} обновлён."))
    return web.json_response({"ok": success, "message" if success else "error": result},
                             status=200 if success else 409)


def register(app: web.Application) -> None:
    app.router.add_get("/api/portal/moderation", cases_list)
    app.router.add_post("/api/portal/moderation", case_create)
    app.router.add_get("/api/portal/moderation/{id}", case_detail)
    app.router.add_post("/api/portal/moderation/{id}/action", case_action)
