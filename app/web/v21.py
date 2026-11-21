"""Versioned member/support/voice contracts on top of the existing local console.

The legacy /api/portal and /api/staff routes stay unchanged in v2.1.0.
All mutations share password authorization, CSRF and the underlying services.
"""
from __future__ import annotations

import json
from pathlib import Path

from aiohttp import web

from app.services import membership_workflow, support_sla, voice_preferences
from app.web.server import json_payload, require_csrf, require_role, state

CONTRACT_VERSION = "1.0"
SCHEMA_FILE = Path(__file__).resolve().parents[2] / "docs" / "api_contract_v1.json"


async def authorized(request: web.Request, *, mutation: bool = False):
    role, member = await require_role(request, "owner")
    if role != "owner":
        raise web.HTTPForbidden(text="Необходим локальный администратор.")
    if mutation:
        require_csrf(request)
    bot = state(request).bot
    actor = member.id if member else getattr(bot.config, "web_local_operator_id", 0)
    return bot, actor


def positive(raw: str) -> int:
    if not isinstance(raw, (str, int)) or isinstance(raw, bool):
        raise web.HTTPBadRequest(text="Неверный идентификатор.")
    raw = str(raw)
    if not raw.isascii() or not raw.isdecimal() or not 0 < int(raw) < 2**64:
        raise web.HTTPBadRequest(text="Неверный идентификатор.")
    return int(raw)


async def contracts(request: web.Request) -> web.Response:
    await authorized(request)
    return web.json_response(json.loads(SCHEMA_FILE.read_text(encoding="utf-8")))


async def forms_get(request: web.Request) -> web.Response:
    bot, _ = await authorized(request)
    return web.json_response({"items": await membership_workflow.form_types(
        bot.database, bot.config.guild_id), "contract": CONTRACT_VERSION})


async def form_save(request: web.Request) -> web.Response:
    bot, actor = await authorized(request, mutation=True)
    if not actor:
        raise web.HTTPServiceUnavailable(text="Нет локального оператора для аудита.")
    data = await json_payload(request)
    try:
        await membership_workflow.save_form(bot.database, bot.config.guild_id,
            key=data.get("key", ""), title=data.get("title", ""),
            question=data.get("question", ""), enabled=data.get("enabled", True))
    except (ValueError, AttributeError, TypeError) as exc:
        raise web.HTTPUnprocessableEntity(text=str(exc)) from exc
    return web.json_response({"ok": True})


async def checklist_get(request: web.Request) -> web.Response:
    bot, _ = await authorized(request)
    aid = positive(request.match_info["id"])
    return web.json_response({"items": await membership_workflow.checklist(
        bot.database, bot.config.guild_id, aid), "contract": CONTRACT_VERSION})


async def checklist_set(request: web.Request) -> web.Response:
    bot, actor = await authorized(request, mutation=True)
    if not actor:
        raise web.HTTPServiceUnavailable(text="Нет локального оператора для аудита.")
    aid = positive(request.match_info["id"])
    data = await json_payload(request)
    try:
        ok = await membership_workflow.set_check(bot.database, bot.config.guild_id, aid,
            key=data.get("key", ""), title=data.get("title", ""),
            completed=data.get("completed"), actor_id=actor)
    except (ValueError, TypeError) as exc:
        raise web.HTTPUnprocessableEntity(text=str(exc)) from exc
    if not ok:
        raise web.HTTPConflict(text="Заявка закрыта или отсутствует.")
    return web.json_response({"ok": True})


async def sla_get(request: web.Request) -> web.Response:
    bot, _ = await authorized(request)
    return web.json_response({"rules": await support_sla.get_rules(
        bot.database, bot.config.guild_id), "contract": CONTRACT_VERSION})


async def sla_save(request: web.Request) -> web.Response:
    bot, actor = await authorized(request, mutation=True)
    if not actor:
        raise web.HTTPServiceUnavailable(text="Не определён локальный оператор.")
    data = await json_payload(request)
    try:
        rules = await support_sla.save_rules(bot.database, bot.config.guild_id,
                                             data.get("rules"), actor_id=actor)
    except ValueError as exc:
        raise web.HTTPUnprocessableEntity(text=str(exc)) from exc
    # Existing scheduled jobs check current rules before sending alerts.
    cog = bot.get_cog("Tickets")
    if cog is not None:
        from app.services import tickets
        for row in await tickets.list_tickets(bot.database, bot.config.guild_id, limit=1000):
            if row.status == "open":
                await cog._schedule_first_reply_alert(row.id)
                await cog._schedule_sla_idle(row.id)
    return web.json_response({"ok": True, "rules": rules})


async def ticket_priority(request: web.Request) -> web.Response:
    bot, actor = await authorized(request, mutation=True)
    if not actor:
        raise web.HTTPServiceUnavailable(text="Нет локального оператора.")
    data = await json_payload(request)
    try:
        ok = await support_sla.change_priority(bot.database, bot.config.guild_id,
            positive(request.match_info["id"]), data.get("priority"), actor_id=actor)
    except ValueError as exc:
        raise web.HTTPUnprocessableEntity(text=str(exc)) from exc
    if not ok:
        raise web.HTTPConflict(text="Тикет закрыт или уже меняется.")
    cog = bot.get_cog("Tickets")
    if cog is not None:
        tid = positive(request.match_info["id"])
        await cog._schedule_first_reply_alert(tid)
        await cog._schedule_sla_idle(tid)
        await cog.refresh(tid)
    return web.json_response({"ok": True})


ONBOARDING_DEFAULT = {
    "rules": "Изучите правила сервера, уважайте других участников и соблюдайте правила каналов.",
    "tasks": ["Прочитайте правила", "Представьтесь команде", "Откройте доступные каналы"],
    "channel_ids": [],
}


def validate_onboarding(data):
    if not isinstance(data, dict) or set(data) != set(ONBOARDING_DEFAULT):
        raise ValueError("Настройки: rules, tasks, channel_ids.")
    rules, tasks, channel_ids = data["rules"], data["tasks"], data["channel_ids"]
    if not isinstance(rules, str) or not 5 <= len(rules) <= 1200:
        raise ValueError("Текст правил: 5–1200 символов.")
    if not isinstance(tasks, list) or not 1 <= len(tasks) <= 8 or any(
        not isinstance(t, str) or not 3 <= len(t) <= 100 for t in tasks):
        raise ValueError("Задания: 1–8 записей по 3–100 символов.")
    if not isinstance(channel_ids, list) or len(channel_ids) > 12:
        raise ValueError("Укажите не больше 12 Discord-каналов.")
    ids = [positive(cid) for cid in channel_ids]
    if len(ids) != len(set(ids)):
        raise ValueError("Каналы не должны повторяться.")
    return {"rules": rules.strip(), "tasks": [x.strip() for x in tasks],
            "channel_ids": ids}


async def onboarding_get(request: web.Request) -> web.Response:
    bot, _ = await authorized(request)
    return web.json_response({"config": await bot.database.get_setting(
        bot.config.guild_id, "membership_onboarding",
        ONBOARDING_DEFAULT), "contract": CONTRACT_VERSION})


async def onboarding_save(request: web.Request) -> web.Response:
    bot, actor = await authorized(request, mutation=True)
    if not actor:
        raise web.HTTPServiceUnavailable(text="Не определён локальный оператор.")
    payload = await json_payload(request)
    try:
        value = validate_onboarding(payload.get("config"))
    except ValueError as exc:
        raise web.HTTPUnprocessableEntity(text=str(exc)) from exc
    await bot.database.set_setting(bot.config.guild_id, "membership_onboarding",
                                   value, actor_id=actor)
    return web.json_response({"ok": True})


async def voice_usage(request: web.Request) -> web.Response:
    bot, _ = await authorized(request)
    days = request.query.get("days", "30")
    if days not in ("7", "14", "30", "90"):
        raise web.HTTPBadRequest(text="Период: 7, 14, 30, 90.")
    return web.json_response({"items": await voice_preferences.usage(
        bot.database, bot.config.guild_id, days=int(days)), "contract": CONTRACT_VERSION})


def register(app: web.Application) -> None:
    app.router.add_get("/api/v1/contracts", contracts)
    app.router.add_get("/api/v1/membership/forms", forms_get)
    app.router.add_post("/api/v1/membership/forms", form_save)
    app.router.add_get("/api/v1/membership/applications/{id}/checklist", checklist_get)
    app.router.add_post("/api/v1/membership/applications/{id}/checklist", checklist_set)
    app.router.add_get("/api/v1/membership/onboarding", onboarding_get)
    app.router.add_post("/api/v1/membership/onboarding", onboarding_save)
    app.router.add_get("/api/v1/support/sla", sla_get)
    app.router.add_post("/api/v1/support/sla", sla_save)
    app.router.add_post("/api/v1/support/tickets/{id}/priority", ticket_priority)
    app.router.add_get("/api/v1/voice/usage", voice_usage)
