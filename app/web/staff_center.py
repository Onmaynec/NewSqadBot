"""Staff Command Center: local-owner workflow APIs, consistent authorization and receipts.

No ticket transcripts or reply bodies are persisted. External Discord effects are
at-least-once in general, so idempotency receipts deliberately require manual
review on uncertain outcomes instead of blindly replaying sends.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone

import discord
from aiohttp import web
from sqlalchemy import func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.core.module_contracts import describe as describe_module_contracts
from app.models import (
    Application,
    ApplicationEvent,
    AuditEntry,
    ModerationAction,
    ModerationCase,
    StaffOperation,
    StaffTask,
    StaffTemplate,
    Ticket,
    TicketCategory,
)
from app.services import applications, tickets
from app.services.diagnostics import collect
from app.web.server import json_payload, require_csrf, require_role, state

OPEN_APPLICATION = ("pending", "in_review", "needs_info", "approving", "rejecting", "action_failed")
OPEN_TICKET = ("open", "creating", "closing")
KEY_FORMAT = re.compile(r"[a-zA-Z0-9_-]{12,72}\Z")
MAX_BATCH = 20


def positive(value, label="Идентификатор") -> int:
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise web.HTTPBadRequest(text=f"{label}: неверный формат.")
    raw = str(value)
    if not raw.isascii() or not raw.isdecimal() or not 0 < int(raw) < 2**64:
        raise web.HTTPBadRequest(text=f"{label}: неверный формат.")
    return int(raw)


def editor(value, maximum: int, label: str, *, minimum: int = 1) -> str:
    if not isinstance(value, str):
        raise web.HTTPUnprocessableEntity(text=f"{label}: ожидается текст.")
    text = value.strip()
    if not minimum <= len(text) <= maximum:
        raise web.HTTPUnprocessableEntity(text=f"{label}: {minimum}–{maximum} символов.")
    return text


async def owner(request: web.Request, *, mutation: bool = False):
    role, member = await require_role(request, "owner")
    if role != "owner":
        raise web.HTTPForbidden(text="Staff Command Center доступен только локальному владельцу.")
    if mutation:
        require_csrf(request)
    return state(request).bot, member


def local_operator_id(bot, member) -> int:
    if member is not None:
        return member.id
    guild = bot.get_guild(bot.config.guild_id)
    return getattr(bot.config, "web_local_operator_id", 0) or (guild.owner_id if guild else 0)


async def log_action(bot, *, actor_id: int, action: str, data: dict) -> None:
    async with bot.database.session() as session:
        session.add(AuditEntry(
            guild_id=bot.config.guild_id, actor_id=actor_id,
            action="staff." + action,
            data_json=json.dumps({"origin": "local_password", **data}, ensure_ascii=False),
            created_at=int(time.time()),
        ))


async def inbox(request: web.Request) -> web.Response:
    bot, member = await owner(request)
    gid = bot.config.guild_id
    operator_id = local_operator_id(bot, member)
    kind = request.query.get("kind", "all")
    assignment = request.query.get("assignment", "all")
    if kind not in ("all", "application", "ticket", "complaint", "moderation"):
        raise web.HTTPBadRequest(text="Недопустимый раздел очереди.")
    if assignment not in ("all", "mine", "unassigned"):
        raise web.HTTPBadRequest(text="Недопустимый фильтр назначения.")
    query = request.query.get("q", "").strip().casefold()
    if len(query) > 80:
        raise web.HTTPBadRequest(text="Слишком длинный поиск.")
    items = []
    async with bot.database.sessions() as session:
        if kind in ("all", "application"):
            apps = (await session.scalars(select(Application).where(
                Application.guild_id == gid, Application.status.in_(OPEN_APPLICATION))
                .order_by(Application.created_at.desc()).limit(150))).all()
            for app in apps:
                items.append({
                    "kind": "application", "id": app.id, "title": app.name,
                    "status": app.status, "assigned_to": str(app.reviewer_id) if app.reviewer_id else None,
                    "user_id": str(app.user_id), "created_at": app.created_at,
                    "link": "Заявки", "details": app.username, "form_key": app.form_key,
                })
        if kind in ("all", "ticket", "complaint"):
            rows = (await session.execute(select(Ticket, TicketCategory.key).join(
                TicketCategory, TicketCategory.id == Ticket.category_id).where(
                Ticket.guild_id == gid, Ticket.status.in_(OPEN_TICKET))
                .order_by(Ticket.updated_at.desc()).limit(150))).all()
            for ticket, category in rows:
                ticket_kind = "complaint" if category == "complaint" else "ticket"
                if kind not in ("all", ticket_kind):
                    continue
                items.append({
                    "kind": ticket_kind, "id": ticket.id, "title": ticket.subject,
                    "status": ticket.status, "assigned_to": str(ticket.assignee_id) if ticket.assignee_id else None,
                    "user_id": str(ticket.user_id), "created_at": ticket.updated_at,
                    "link": "Тикеты", "details": "Жалоба" if ticket_kind == "complaint" else "Поддержка",
                    "priority": ticket.priority,
                })
        if kind in ("all", "moderation"):
            cases = (await session.scalars(select(ModerationCase).where(
                ModerationCase.guild_id == gid, ModerationCase.status.in_(("pending", "active")),
            ).order_by(ModerationCase.created_at.desc()).limit(100))).all()
            for case in cases:
                items.append({
                    "kind": "moderation", "id": case.id, "title": case.kind,
                    "status": case.status, "assigned_to": str(case.moderator_id) if case.moderator_id else None,
                    "user_id": str(case.user_id), "created_at": case.created_at,
                    "link": "Модерация", "details": "Открытое дело",
                })
    if assignment == "mine":
        items = [item for item in items if item["assigned_to"] == str(operator_id) and operator_id > 0]
    elif assignment == "unassigned":
        items = [item for item in items if item["assigned_to"] is None]
    if query:
        items = [item for item in items if query in
                 (" ".join(str(item[k]) for k in ("id", "title", "user_id", "details"))).casefold()]
    rank = {"urgent": 4, "high": 3, "normal": 2, "low": 1}
    items.sort(key=lambda item: (rank.get(item.get("priority"), 0),
                                 item["created_at"], item["id"]), reverse=True)
    return web.json_response({"items": items[:200], "total": len(items),
                              "operator_id": str(operator_id) if operator_id else None,
                              "counts": {key: sum(row["kind"] == key for row in items)
                                         for key in ("application", "ticket", "complaint", "moderation")}})


def public_task(row: StaffTask) -> dict:
    return {"id": row.id, "title": row.title, "description": row.description,
            "assigned_to": str(row.assigned_to), "created_by": str(row.created_by),
            "status": row.status, "due_at": row.due_at,
            "created_at": row.created_at, "updated_at": row.updated_at}


async def tasks_list(request: web.Request) -> web.Response:
    bot, member = await owner(request)
    operator_id = local_operator_id(bot, member)
    only_mine = request.query.get("mine", "1") != "0"
    async with bot.database.sessions() as session:
        conditions = [StaffTask.guild_id == bot.config.guild_id]
        if only_mine:
            conditions.append(StaffTask.assigned_to == operator_id)
        tasks = (await session.scalars(select(StaffTask).where(*conditions)
                 .order_by(StaffTask.status, StaffTask.due_at, StaffTask.id.desc()).limit(150))).all()
    return web.json_response({"items": [public_task(x) for x in tasks],
                              "operator_id": str(operator_id) if operator_id else None})


async def task_create(request: web.Request) -> web.Response:
    bot, member = await owner(request, mutation=True)
    body = await json_payload(request)
    operator = local_operator_id(bot, member)
    if not operator:
        raise web.HTTPServiceUnavailable(text="Локальный оператор не настроен.")
    title = editor(body.get("title"), 120, "Название")
    description = editor(body.get("description", ""), 600, "Описание", minimum=0)
    assignee = positive(body.get("assigned_to") or operator, "Сотрудник")
    due = body.get("due_at")
    if due is not None and (not isinstance(due, int) or isinstance(due, bool)
                            or not 0 <= due < 2**40):
        raise web.HTTPBadRequest(text="Некорректный срок задачи.")
    now = int(time.time())
    async with bot.database.session() as session:
        task = StaffTask(guild_id=bot.config.guild_id, assigned_to=assignee, created_by=operator,
                         title=title, description=description, status="open", due_at=due,
                         created_at=now, updated_at=now)
        session.add(task)
        await session.flush()
        task_id = task.id
    await log_action(bot, actor_id=operator, action="task_created", data={"task_id": task_id, "to": str(assignee)})
    return web.json_response({"ok": True, "id": task_id})


async def task_status(request: web.Request) -> web.Response:
    bot, member = await owner(request, mutation=True)
    operator = local_operator_id(bot, member)
    task_id = positive(request.match_info["id"])
    body = await json_payload(request)
    status = body.get("status")
    if status not in ("open", "done"):
        raise web.HTTPBadRequest(text="Недопустимый статус задачи.")
    async with bot.database.session() as session:
        result = await session.execute(update(StaffTask).where(
            StaffTask.guild_id == bot.config.guild_id,
            StaffTask.id == task_id, StaffTask.status != status,
        ).values(status=status, updated_at=int(time.time())))
        changed = result.rowcount == 1
    if changed:
        await log_action(bot, actor_id=operator, action="task_updated",
                         data={"task_id": task_id, "status": status})
    return web.json_response({"ok": changed}, status=200 if changed else 409)


async def templates_list(request: web.Request) -> web.Response:
    bot, _ = await owner(request)
    async with bot.database.sessions() as session:
        rows = (await session.scalars(select(StaffTemplate).where(
            StaffTemplate.guild_id == bot.config.guild_id)
            .order_by(StaffTemplate.scope, StaffTemplate.title).limit(120))).all()
    return web.json_response({"items": [
        {"id": x.id, "scope": x.scope, "title": x.title, "body": x.body, "updated_at": x.updated_at}
        for x in rows]})


async def template_save(request: web.Request) -> web.Response:
    bot, member = await owner(request, mutation=True)
    payload = await json_payload(request)
    scope = payload.get("scope")
    if scope not in ("ticket", "application"):
        raise web.HTTPUnprocessableEntity(text="Неизвестный тип шаблона.")
    title = editor(payload.get("title"), 100, "Название шаблона")
    body = editor(payload.get("body"), 1500 if scope == "ticket" else 500, "Текст")
    template_id = payload.get("id")
    actor = local_operator_id(bot, member)
    now = int(time.time())
    async with bot.database.session() as session:
        if template_id is None:
            row = await session.scalar(select(StaffTemplate).where(
                StaffTemplate.guild_id == bot.config.guild_id,
                StaffTemplate.scope == scope, StaffTemplate.title == title))
            if row is not None:
                raise web.HTTPConflict(text="Шаблон с таким названием уже существует.")
            row = StaffTemplate(guild_id=bot.config.guild_id, scope=scope, title=title,
                                body=body, updated_by=actor, updated_at=now)
            session.add(row)
        else:
            row = await session.scalar(select(StaffTemplate).where(
                StaffTemplate.guild_id == bot.config.guild_id, StaffTemplate.id == positive(template_id)))
            if row is None:
                raise web.HTTPNotFound(text="Шаблон не найден.")
            row.scope, row.title, row.body, row.updated_by, row.updated_at = scope, title, body, actor, now
        await session.flush()
        tid = row.id
    await log_action(bot, actor_id=actor, action="template_saved", data={"template_id": tid})
    return web.json_response({"ok": True, "id": tid})


async def template_delete(request: web.Request) -> web.Response:
    from sqlalchemy import delete

    bot, member = await owner(request, mutation=True)
    template_id = positive(request.match_info["id"])
    async with bot.database.session() as session:
        result = await session.execute(delete(StaffTemplate).where(
            StaffTemplate.guild_id == bot.config.guild_id, StaffTemplate.id == template_id))
        deleted = result.rowcount == 1
    if deleted:
        await log_action(bot, actor_id=local_operator_id(bot, member),
                         action="template_deleted", data={"template_id": template_id})
    return web.json_response({"ok": deleted}, status=200 if deleted else 404)


async def timeline(request: web.Request) -> web.Response:
    bot, _ = await owner(request)
    uid = positive(request.query.get("user_id", ""), "Discord ID сотрудника")
    gid = bot.config.guild_id
    events = []
    async with bot.database.sessions() as session:
        audit = (await session.scalars(select(AuditEntry).where(
            AuditEntry.guild_id == gid, AuditEntry.actor_id == uid)
            .order_by(AuditEntry.created_at.desc()).limit(100))).all()
        for x in audit:
            events.append({"at": x.created_at, "kind": "audit", "action": x.action,
                           "target": "", "detail": "Системное действие"})
        app_events = (await session.execute(select(ApplicationEvent, Application.id).join(
            Application, ApplicationEvent.application_id == Application.id).where(
            Application.guild_id == gid, ApplicationEvent.actor_id == uid)
            .order_by(ApplicationEvent.created_at.desc()).limit(100))).all()
        for x, aid in app_events:
            events.append({"at": x.created_at, "kind": "application",
                           "action": x.new_status, "target": str(aid), "detail": "Заявка"})
        moderation = (await session.scalars(select(ModerationAction).join(
            ModerationCase, ModerationAction.case_id == ModerationCase.id).where(
            ModerationCase.guild_id == gid, ModerationAction.actor_id == uid)
            .order_by(ModerationAction.created_at.desc()).limit(100))).all()
        for x in moderation:
            events.append({"at": x.created_at, "kind": "moderation",
                           "action": x.action, "target": str(x.case_id), "detail": "Модерация"})
        operations = (await session.scalars(select(StaffOperation).where(
            StaffOperation.guild_id == gid, StaffOperation.actor_id == uid)
            .order_by(StaffOperation.created_at.desc()).limit(100))).all()
        for x in operations:
            events.append({"at": x.created_at, "kind": "staff",
                           "action": x.action + " · " + x.status,
                           "target": str(x.target_id), "detail": x.target_kind})
    events.sort(key=lambda x: x["at"], reverse=True)
    return web.json_response({"user_id": str(uid), "items": events[:120]})


async def analytics(request: web.Request) -> web.Response:
    bot, _ = await owner(request)
    days_raw = request.query.get("days", "30")
    if days_raw not in ("7", "14", "30", "90"):
        raise web.HTTPBadRequest(text="Период: 7, 14, 30 или 90 дней.")
    days = int(days_raw)
    now = int(time.time())
    since = now - days * 86400
    gid = bot.config.guild_id
    async with bot.database.sessions() as session:
        apps = (await session.scalars(select(Application).where(
            Application.guild_id == gid, Application.created_at >= since))).all()
        tickets_rows = (await session.scalars(select(Ticket).where(
            Ticket.guild_id == gid, Ticket.opened_at >= since))).all()
        mod_cases = (await session.scalars(select(ModerationCase).where(
            ModerationCase.guild_id == gid, ModerationCase.created_at >= since))).all()
        active_apps = (await session.execute(select(Application.reviewer_id, func.count()).where(
            Application.guild_id == gid, Application.status.in_(OPEN_APPLICATION),
            Application.reviewer_id.is_not(None)).group_by(Application.reviewer_id))).all()
        active_tickets = (await session.execute(select(Ticket.assignee_id, func.count()).where(
            Ticket.guild_id == gid, Ticket.status.in_(OPEN_TICKET),
            Ticket.assignee_id.is_not(None)).group_by(Ticket.assignee_id))).all()
    start_date = datetime.fromtimestamp(since, timezone.utc).date()
    trend = {}
    for i in range(days + 1):
        key = start_date.fromordinal(start_date.toordinal() + i).isoformat()
        trend[key] = {"day": key, "applications": 0, "tickets": 0, "moderation": 0}
    for kind, records, attribute in (
        ("applications", apps, "created_at"),
        ("tickets", tickets_rows, "opened_at"),
        ("moderation", mod_cases, "created_at"),
    ):
        for row in records:
            date = datetime.fromtimestamp(getattr(row, attribute), timezone.utc).date().isoformat()
            if date in trend:
                trend[date][kind] += 1
    durations = [row.first_response_at - row.opened_at for row in tickets_rows
                 if row.first_response_at is not None and row.first_response_at >= row.opened_at]
    staff = {}
    for label, rows in (("applications", active_apps), ("tickets", active_tickets)):
        for uid, amount in rows:
            staff.setdefault(str(uid), {"user_id": str(uid), "applications": 0, "tickets": 0})[label] = amount
    return web.json_response({
        "period_days": days, "trend": list(trend.values()),
        "totals": {"applications": len(apps), "tickets": len(tickets_rows), "moderation": len(mod_cases)},
        "avg_first_response_seconds": round(sum(durations) / len(durations)) if durations else None,
        "response_sample": len(durations), "staff": sorted(staff.values(),
            key=lambda x: x["applications"] + x["tickets"], reverse=True)[:60],
    })


async def health_center(request: web.Request) -> web.Response:
    bot, _ = await owner(request)
    values = []
    try:
        values = [{"level": v.level, "name": v.name, "description": v.description}
                  for v in await collect(bot)]
    except Exception:
        values = [{"level": "warning", "name": "Диагностика",
                   "description": "Сбор данных невозможен. Проверьте Docker-логи."}]
    operations = getattr(bot, "operations", None)
    return web.json_response({
        "items": values, "operations": operations.snapshot() if operations else {},
        "modules": bot.module_states,
        "module_contracts": describe_module_contracts(bot.module_states),
        "gateway_ready": bot.is_ready(),
    })


async def _reserve(bot, *, request_key: str, kind: str, target_id: int, action: str, actor: int) -> bool:
    now = int(time.time())
    async with bot.database.session() as session:
        result = await session.execute(sqlite_insert(StaffOperation).values(
            guild_id=bot.config.guild_id, request_key=request_key, target_kind=kind,
            target_id=target_id, action=action, actor_id=actor, status="processing",
            summary="", created_at=now, updated_at=now,
        ).on_conflict_do_nothing(
            index_elements=["guild_id", "request_key", "target_kind", "target_id"]))
        return result.rowcount == 1


async def _finish(bot, *, request_key: str, kind: str, target_id: int,
                  status: str, summary: str, actor: int, action: str) -> None:
    # A result is persisted even if the HTTP response is disconnected. Never retry
    # a previous key without operator review of the real Discord state.
    async with bot.database.session() as session:
        await session.execute(update(StaffOperation).where(
            StaffOperation.guild_id == bot.config.guild_id,
            StaffOperation.request_key == request_key,
            StaffOperation.target_kind == kind,
            StaffOperation.target_id == target_id,
            StaffOperation.status == "processing",
        ).values(status=status, summary=summary[:240], updated_at=int(time.time())))
        session.add(AuditEntry(
            guild_id=bot.config.guild_id, actor_id=actor,
            action="staff.operation",
            data_json=json.dumps({"origin": "local_password", "kind": kind,
                                  "target_id": target_id, "action": action,
                                  "status": status}, ensure_ascii=False),
            created_at=int(time.time()),
        ))


async def _application_action(bot, aid: int, action: str, actor: int, reason: str) -> tuple[bool, str]:
    row = await applications.get_application(bot.database, aid)
    if row is None or row.guild_id != bot.config.guild_id:
        return False, "Заявка не найдена."
    if action == "claim":
        ok = await applications.assign_reviewer(bot.database, bot.config.guild_id, aid, actor, actor)
        cog = bot.get_cog("Membership")
        if ok and cog is not None:
            await cog.schedule_review_reminder(await applications.get_application(bot.database, aid))
        return ok, "Назначен ответственный." if ok else "Заявка недоступна для назначения."
    if action not in ("approve", "reject"):
        return False, "Недопустимое действие."
    cog = bot.get_cog("Membership")
    if cog is None or not bot.is_ready():
        return False, "Discord или модуль заявок недоступен."
    if action == "reject" and not 3 <= len(reason) <= 400:
        return False, "Нужна причина отказа (3–400 символов)."
    target = "approving" if action == "approve" else "rejecting"
    ok = await applications.transition(
        bot.database, aid, expected=applications.REVIEWABLE,
        target=target, actor_id=actor,
        reason=reason if action == "reject" else "Одобрено через локальный сайт",
        decision="approve" if action == "approve" else "reject",
    )
    if not ok:
        return False, "Решение уже обрабатывается или принято."
    result = await cog.execute(aid)
    await cog.refresh(aid)
    latest = await applications.get_application(bot.database, aid)
    confirmed = bool(latest and latest.status == ("approved" if action == "approve" else "rejected"))
    return confirmed, result


async def bulk(request: web.Request) -> web.Response:
    bot, member = await owner(request, mutation=True)
    payload = await json_payload(request)
    key, action, ids = payload.get("request_key"), payload.get("action"), payload.get("ids")
    if not isinstance(key, str) or not KEY_FORMAT.fullmatch(key):
        raise web.HTTPBadRequest(text="Для защиты от повторов нужен случайный request_key.")
    if action not in ("claim", "approve", "reject"):
        raise web.HTTPBadRequest(text="Допустимы только claim, approve, reject.")
    if not isinstance(ids, list) or not 1 <= len(ids) <= MAX_BATCH:
        raise web.HTTPBadRequest(text="Выберите от 1 до 20 заявок.")
    values = [positive(i, "Заявка") for i in ids]
    if len(values) != len(set(values)):
        raise web.HTTPBadRequest(text="Номера заявок повторяются.")
    if payload.get("confirm") != "CONFIRM":
        raise web.HTTPUnprocessableEntity(text="Подтвердите массовое действие.")
    reason = payload.get("reason", "")
    if not isinstance(reason, str) or len(reason) > 400:
        raise web.HTTPBadRequest(text="Недопустимая причина.")
    reason = reason.strip()
    if action == "reject" and len(reason) < 3:
        raise web.HTTPUnprocessableEntity(text="Для отказа укажите причину.")
    actor = local_operator_id(bot, member)
    if member is None or not actor:
        raise web.HTTPServiceUnavailable(text="Оператор Discord не определён. Изменения не выполнялись.")
    results = []
    for aid in values:
        if not await _reserve(bot, request_key=key, kind="application",
                              target_id=aid, action=action, actor=actor):
            results.append({"id": aid, "status": "duplicate",
                            "message": "Уже обрабатывалось с этим ключом; проверьте историю."})
            continue
        try:
            ok, message = await _application_action(bot, aid, action, actor, reason)
            # Discord actions may have succeeded before a DB failure or lost response.
            result_status = "success" if ok else "needs_review"
        except Exception:
            # A prior external Discord action may already have taken effect.
            result_status, message = "needs_review", "Неизвестный результат; проверьте Discord вручную."
        await _finish(bot, request_key=key, kind="application", target_id=aid,
                      status=result_status, summary=message, actor=actor, action=action)
        results.append({"id": aid, "status": result_status, "message": message})
    return web.json_response({"items": results, "request_key": key,
                              "success": sum(item["status"] == "success" for item in results),
                              "needs_review": sum(item["status"] != "success" for item in results)})


async def response_send(request: web.Request) -> web.Response:
    bot, member = await owner(request, mutation=True)
    if member is None:
        raise web.HTTPServiceUnavailable(text="Локальный оператор недоступен.")
    payload = await json_payload(request)
    kind = payload.get("kind")
    if kind not in ("ticket", "application"):
        raise web.HTTPBadRequest(text="Недопустимый тип обращения.")
    target_id = positive(payload.get("id"))
    body = editor(payload.get("text"), 1500 if kind == "ticket" else 500, "Ответ",
                  minimum=5 if kind == "application" else 1)
    key = payload.get("request_key")
    if not isinstance(key, str) or not KEY_FORMAT.fullmatch(key):
        raise web.HTTPBadRequest(text="Нужен уникальный request_key.")
    if not await _reserve(bot, request_key=key, kind=kind,
                          target_id=target_id, action="reply", actor=member.id):
        return web.json_response({"error": "Ответ с таким ключом уже обрабатывался."}, status=409)
    result_status, message = "needs_review", "Отправка не подтверждена."
    try:
        if kind == "ticket":
            row = await tickets.get(bot.database, bot.config.guild_id, target_id)
            if row is None or row.status != "open":
                result_status, message = "failed", "Открытый тикет не найден."
            else:
                from app.web.portal import _ticket_channel

                channel = await _ticket_channel(bot, row)
                label = discord.utils.escape_markdown(discord.utils.escape_mentions(
                    "Локальная администрация"))
                await channel.send(f"**Ответ от {label}:**\n{body}",
                                   allowed_mentions=discord.AllowedMentions.none())
                # Do not send another Discord message even if timestamp update fails.
                result_status, message = "needs_review", "Ответ отправлен, обновление статистики не подтверждено."
                await tickets.touch(bot.database, bot.config.guild_id, target_id,
                                    member.id, staff_reply=True)
                result_status, message = "success", "Ответ отправлен в Discord."
        else:
            row = await applications.get_application(bot.database, target_id)
            if row is None or row.guild_id != bot.config.guild_id:
                result_status, message = "failed", "Заявка не найдена."
            else:
                ok = await applications.transition(
                    bot.database, target_id, expected=("pending", "in_review"),
                    target="needs_info", actor_id=member.id,
                    reason="Запрос уточнения через локальный Staff Center")
                if not ok:
                    result_status, message = "failed", "Заявка уже ожидает ответа или закрыта."
                else:
                    user = bot.get_user(row.user_id) or await bot.fetch_user(row.user_id)
                    await user.send("NewSqad: заявка #" + str(target_id) + " — уточните: " + body,
                                    allowed_mentions=discord.AllowedMentions.none())
                    result_status, message = "success", "Уточнение отправлено кандидату."
    except discord.Forbidden:
        # Especially important for applications: state may already say needs_info.
        result_status, message = "needs_review", "Discord отказал. Проверьте права и состояние обращения."
    except (discord.HTTPException, OSError, TimeoutError):
        result_status, message = "needs_review", "Discord не подтвердил отправку; не отправляйте повторно вслепую."
    except Exception:
        result_status, message = "needs_review", "Операция завершилась неясно; проверьте канал и журнал."
    await _finish(bot, request_key=key, kind=kind, target_id=target_id, status=result_status,
                  summary=message, actor=member.id, action="reply")
    return web.json_response({"ok": result_status == "success", "status": result_status,
                              "message": message}, status=200 if result_status == "success" else 409)


def register(app: web.Application) -> None:
    app.router.add_get("/api/staff/inbox", inbox)
    app.router.add_get("/api/staff/tasks", tasks_list)
    app.router.add_post("/api/staff/tasks", task_create)
    app.router.add_post("/api/staff/tasks/{id}/status", task_status)
    app.router.add_get("/api/staff/templates", templates_list)
    app.router.add_post("/api/staff/templates", template_save)
    app.router.add_post("/api/staff/templates/{id}/delete", template_delete)
    app.router.add_get("/api/staff/timeline", timeline)
    app.router.add_get("/api/staff/analytics", analytics)
    app.router.add_get("/api/staff/health", health_center)
    app.router.add_post("/api/staff/bulk", bulk)
    app.router.add_post("/api/staff/reply", response_send)
