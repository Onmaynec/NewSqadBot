"""Owner-only editors for shop, self-role panels, ticket categories and announcements."""
from __future__ import annotations

import asyncio
import logging
import time

import discord
from aiohttp import web
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from app.models import AuditEntry, LevelRoleReward, RolePanel, RolePanelOption
from app.services import announcements, economy, tickets
from app.services.role_rules import validate_panel
from app.web.moderation_portal import identifier, number
from app.web.server import guild_for, json_payload, require_csrf, require_role, state

log = logging.getLogger(__name__)
PANEL_LOCKS: dict[int, asyncio.Lock] = {}


async def owner(request):
    _, actor = await require_role(request, "owner")
    # The old localhost password panel has no attributable Discord actor.
    # Configuration-only operations may use actor 0; Discord publication cannot.
    from types import SimpleNamespace

    return state(request).bot, actor or SimpleNamespace(id=0)


def text_field(payload, key: str, minimum: int, maximum: int) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum:
        raise web.HTTPUnprocessableEntity(text=f"Поле {key}: {minimum}–{maximum} символов.")
    return value.strip()


def int_field(payload, key: str, minimum: int, maximum: int, *, default=None) -> int:
    value = payload.get(key, default)
    if type(value) is not int or not minimum <= value <= maximum:
        raise web.HTTPUnprocessableEntity(text=f"Поле {key}: целое число {minimum}–{maximum}.")
    return value


async def audit_owner(bot, actor: int, action: str, number_value: int) -> None:
    try:
        async with bot.database.session() as session:
            session.add(AuditEntry(guild_id=bot.config.guild_id, actor_id=actor,
                                   action=action,
                                   data_json='{"id":'+str(number_value)+',"source":"web"}',
                                   created_at=int(time.time())))
    except Exception:
        log.exception("Owner operation %s applied but audit failed", action)


def shop_item(item) -> dict:
    return {"id": item.id, "code": item.code, "title": item.title, "description": item.description,
            "kind": item.kind, "price": item.price, "stock": item.stock, "sold": item.sold,
            "role_id": str(item.role_id) if item.role_id else None, "active": item.active}


async def shop_list(request: web.Request) -> web.Response:
    bot, _ = await owner(request)
    items = await economy.items(bot.database, bot.config.guild_id, active_only=False)
    return web.json_response({"items": [shop_item(item) for item in items]})


async def shop_create(request: web.Request) -> web.Response:
    bot, actor = await owner(request)
    require_csrf(request)
    data = await json_payload(request)
    kind = data.get("kind")
    price = int_field(data, "price", 1, 1000000)
    stock = data.get("stock")
    if stock is not None:
        stock = int_field(data, "stock", 1, 1000000)
    role_id = None
    if kind == "role":
        if not isinstance(data.get("role_id"), str):
            raise web.HTTPUnprocessableEntity(text="Выберите декоративную роль.")
        role_id = identifier(data["role_id"])
        guild = guild_for(bot)
        selected = guild.get_role(role_id) if guild else None
        economy_cog = bot.get_cog("Economy")
        if selected is None or economy_cog is None or not await economy_cog._role_safe(guild, selected):
            raise web.HTTPForbidden(text="Роль небезопасна для продажи или выше роли бота.")
    elif kind != "title" or data.get("role_id"):
        raise web.HTTPUnprocessableEntity(text="Выберите title или role.")
    try:
        item = await economy.add_item(
            bot.database, guild_id=bot.config.guild_id,
            code=text_field(data, "code", 2, 40),
            title=text_field(data, "title", 1, 80),
            kind=kind, price=price,
            description=data.get("description", "") if isinstance(data.get("description", ""), str) else "",
            role_id=role_id, stock=stock)
    except ValueError as exc:
        raise web.HTTPUnprocessableEntity(text=str(exc)) from exc
    await audit_owner(bot, actor.id, "web.shop.created", item.id)
    return web.json_response({"ok": True, "item": shop_item(item)})


async def shop_change(request: web.Request) -> web.Response:
    bot, actor = await owner(request)
    require_csrf(request)
    item_id = number(request.match_info["id"])
    payload = await json_payload(request)
    if type(payload.get("active")) is not bool:
        raise web.HTTPUnprocessableEntity(text="Необходимо active=true/false.")
    changed = await economy.set_item_active(bot.database, bot.config.guild_id, item_id, payload["active"])
    if not changed:
        raise web.HTTPNotFound(text="Товар не найден.")
    await audit_owner(bot, actor.id, "web.shop.active", item_id)
    return web.json_response({"ok": True})


def panel_card(row, options: list) -> dict:
    return {"id": row.id, "channel_id": str(row.channel_id), "message_id":
            str(row.message_id) if row.message_id else None,
            "title": row.title, "description": row.description,
            "mode": row.mode, "enabled": row.enabled,
            "options": [{"role_id": str(o.role_id), "label": o.label, "category": o.category}
                        for o in options]}


async def role_panels(request: web.Request) -> web.Response:
    bot, _ = await owner(request)
    async with bot.database.sessions() as session:
        rows = (await session.scalars(select(RolePanel).where(
            RolePanel.guild_id == bot.config.guild_id).order_by(RolePanel.id.desc()).limit(100))).all()
        cards = []
        for row in rows:
            options = (await session.scalars(select(RolePanelOption).where(
                RolePanelOption.panel_id == row.id).order_by(RolePanelOption.id))).all()
            result = panel_card(row, options)
            result["exclusions"] = [[str(v) for v in pair] for pair in await bot.database.get_setting(
                bot.config.guild_id, f"roles.exclusions.{row.id}", [])]
            cards.append(result)
    return web.json_response({"items": cards})


async def role_panel_create(request: web.Request) -> web.Response:
    bot, actor = await owner(request)
    require_csrf(request)
    data = await json_payload(request)
    mode = data.get("mode")
    if mode not in ("multi", "single"):
        raise web.HTTPUnprocessableEntity(text="Выберите multi/single.")
    guild = guild_for(bot)
    channel_id = identifier(data.get("channel_id"))
    channel = guild.get_channel(channel_id) if guild else None
    if not isinstance(channel, discord.TextChannel):
        raise web.HTTPUnprocessableEntity(text="Выберите существующий текстовый канал.")
    me = guild.me
    if me is None or not all((channel.permissions_for(me).view_channel,
                              channel.permissions_for(me).send_messages,
                              channel.permissions_for(me).embed_links)):
        raise web.HTTPForbidden(text="Бот не может отправлять Embed в этот канал.")
    async with bot.database.session() as session:
        row = RolePanel(guild_id=bot.config.guild_id, channel_id=channel_id,
                        message_id=None, title=text_field(data, "title", 1, 100),
                        description=text_field(data, "description", 1, 1000),
                        mode=mode, enabled=True, created_by=actor.id, created_at=int(time.time()))
        session.add(row)
        await session.flush()
        pid = row.id
    await audit_owner(bot, actor.id, "web.role_panel.created", pid)
    return web.json_response({"ok": True, "id": pid})


async def role_panel_action(request: web.Request) -> web.Response:
    bot, actor = await owner(request)
    require_csrf(request)
    pid = number(request.match_info["id"])
    data = await json_payload(request)
    action = data.get("action")
    cog = bot.get_cog("Roles")
    if cog is None:
        raise web.HTTPServiceUnavailable(text="Модуль ролей не загружен.")
    lock = PANEL_LOCKS.setdefault(pid, asyncio.Lock())
    async with lock:
        row, options = await cog._panel_data(pid)
        if row is None or row.guild_id != bot.config.guild_id:
            raise web.HTTPNotFound(text="Панель не найдена.")
        if action == "disable":
            async with bot.database.session() as session:
                await session.execute(update(RolePanel).where(
                    RolePanel.guild_id == bot.config.guild_id, RolePanel.id == pid).values(enabled=False))
            if row.message_id:
                guild = guild_for(bot)
                channel = guild.get_channel(row.channel_id) if guild else None
                if isinstance(channel, discord.TextChannel):
                    try:
                        msg = await channel.fetch_message(row.message_id)
                        await msg.edit(view=None, allowed_mentions=discord.AllowedMentions.none())
                    except discord.HTTPException:
                        log.warning("Панель #%s отключена в SQLite, но Discord-кнопки не сняты", pid)
        elif action == "add":
            if not row.enabled or len(options) >= 25:
                raise web.HTTPConflict(text="Панель отключена или заполнена (25 ролей).")
            role_id = identifier(data.get("role_id"))
            guild = guild_for(bot)
            role = guild.get_role(role_id) if guild else None
            if role is None or not await cog._role_allowed(guild, role):
                raise web.HTTPForbidden(text="Роль опасна или недоступна для выдачи.")
            category = data.get("category")
            if category not in ("games", "notifications", "interests", "decorative"):
                raise web.HTTPUnprocessableEntity(text="Недопустимая категория роли.")
            label = text_field(data, "label", 1, 80)
            try:
                async with bot.database.session() as session:
                    session.add(RolePanelOption(panel_id=pid, role_id=role_id,
                                                label=label, category=category))
            except IntegrityError as exc:
                raise web.HTTPConflict(text="Роль уже включена в панель.") from exc
            await cog._refresh_published(pid)
        elif action == "remove":
            role_id = identifier(data.get("role_id"))
            async with bot.database.session() as session:
                result = await session.execute(delete(RolePanelOption).where(
                    RolePanelOption.panel_id == pid, RolePanelOption.role_id == role_id))
            if not result.rowcount:
                raise web.HTTPNotFound(text="Роль в панели не найдена.")
            await cog._refresh_published(pid)
        elif action == "exclude":
            allowed = {opt.role_id for opt in options}
            first = identifier(data.get("first"))
            second = identifier(data.get("second"))
            if first == second or first not in allowed or second not in allowed:
                raise web.HTTPUnprocessableEntity(text="Укажите две разные роли из панели.")
            if type(data.get("enabled")) is not bool:
                raise web.HTTPUnprocessableEntity(text="Необходимо enabled=true/false.")
            key = f"roles.exclusions.{pid}"
            pairs = await bot.database.get_setting(bot.config.guild_id, key, [])
            pair = sorted((first, second))
            pairs = [p for p in pairs if sorted(p) != pair]
            if data["enabled"]:
                pairs.append(pair)
            await bot.database.set_setting(bot.config.guild_id, key, pairs, actor_id=actor.id)
        elif action == "publish":
            from app.modules.roles import RolePanelView, panel_embed

            validate_panel(row.mode, len(options))
            guild = guild_for(bot)
            channel = guild.get_channel(row.channel_id) if guild else None
            if not isinstance(channel, discord.TextChannel):
                raise web.HTTPConflict(text="Канал панели удалён.")
            if row.message_id:
                if not await cog._refresh_published(pid):
                    raise web.HTTPServiceUnavailable(text="Discord не подтвердил обновление сообщения.")
            else:
                try:
                    message = await channel.send(
                        embed=panel_embed(row), view=RolePanelView(cog, row, options),
                        allowed_mentions=discord.AllowedMentions.none())
                except discord.HTTPException as exc:
                    raise web.HTTPServiceUnavailable(text="Discord не опубликовал панель.") from exc
                async with bot.database.session() as session:
                    result = await session.execute(update(RolePanel).where(
                        RolePanel.guild_id == bot.config.guild_id, RolePanel.id == pid,
                        RolePanel.message_id.is_(None)).values(message_id=message.id))
                    if result.rowcount != 1:
                        raise web.HTTPConflict(text="Карточка могла быть опубликована параллельно.")
                cog.bot.add_view(RolePanelView(cog, row, options), message_id=message.id)
        else:
            raise web.HTTPBadRequest(text="Неизвестная операция с панелью ролей.")
    await audit_owner(bot, actor.id, f"web.role_panel.{action}", pid)
    return web.json_response({"ok": True, "message": "Панель ролей обновлена."})


def public_category(row) -> dict:
    return {"id": row.id, "key": row.key, "title": row.title,
            "description": row.description, "candidate_allowed": row.candidate_allowed,
            "enabled": row.enabled, "support_role_id": str(row.support_role_id) if row.support_role_id else None}


async def categories_list(request: web.Request) -> web.Response:
    bot, _ = await owner(request)
    rows = await tickets.categories(bot.database, bot.config.guild_id, enabled_only=False)
    return web.json_response({"items": [public_category(row) for row in rows]})


async def categories_create(request: web.Request) -> web.Response:
    bot, actor = await owner(request)
    require_csrf(request)
    data = await json_payload(request)
    rows = await tickets.categories(bot.database, bot.config.guild_id, enabled_only=False)
    if len(rows) >= 25:
        raise web.HTTPConflict(text="Максимум 25 категорий.")
    support_id = data.get("support_role_id")
    support = identifier(support_id) if support_id else None
    guild = guild_for(bot)
    if support and (guild is None or guild.get_role(support) is None):
        raise web.HTTPUnprocessableEntity(text="Роль поддержки удалена.")
    try:
        row = await tickets.add_category(bot.database, bot.config.guild_id,
            text_field(data, "key", 1, 40), text_field(data, "title", 1, 90),
            text_field(data, "description", 1, 300),
            support_role_id=support,
            candidate_allowed=data.get("candidate_allowed") is True)
    except ValueError as exc:
        raise web.HTTPUnprocessableEntity(text=str(exc)) from exc
    await audit_owner(bot, actor.id, "web.ticket_category.created", row.id)
    return web.json_response({"ok": True, "item": public_category(row)})


async def categories_change(request: web.Request) -> web.Response:
    bot, actor = await owner(request)
    require_csrf(request)
    data = await json_payload(request)
    category_id = number(request.match_info["id"])
    if (type(data.get("enabled")) is not bool or
        type(data.get("candidate_allowed")) is not bool):
        raise web.HTTPUnprocessableEntity(text="Параметры категории должны быть true/false.")
    changed = await tickets.update_category(
        bot.database, bot.config.guild_id, category_id,
        enabled=data["enabled"], candidate_allowed=data["candidate_allowed"])
    if not changed:
        raise web.HTTPNotFound(text="Категория не найдена.")
    await audit_owner(bot, actor.id, "web.ticket_category.updated", category_id)
    return web.json_response({"ok": True})


def announcement_card(row) -> dict:
    return {"id": row.id, "title": row.title, "body": row.body, "kind": row.kind,
            "channel_id": str(row.channel_id), "status": row.status, "due_at": row.due_at,
            "color": row.color, "error": row.error, "message_id":
            str(row.message_id) if row.message_id else None}


async def announcement_list(request: web.Request) -> web.Response:
    bot, _ = await owner(request)
    rows = await announcements.list_items(bot.database, bot.config.guild_id,
        statuses=("draft", "scheduled", "sending", "sent", "uncertain", "failed", "cancelled"), limit=100)
    templates = await announcements.templates(bot.database, bot.config.guild_id)
    return web.json_response({"items": [announcement_card(row) for row in rows],
                              "templates": [{"slug": x.slug, "title": x.title, "kind": x.kind}
                                            for x in templates]})


async def announcement_create(request: web.Request) -> web.Response:
    bot, actor = await owner(request)
    require_csrf(request)
    if actor.id == 0:
        raise web.HTTPForbidden(text="Не удалось определить локального оператора Discord.")
    data = await json_payload(request)
    guild = guild_for(bot)
    cid = identifier(data.get("channel_id"))
    channel = guild.get_channel(cid) if guild else None
    if not isinstance(channel, discord.TextChannel):
        raise web.HTTPUnprocessableEntity(text="Выберите текстовый канал для объявления.")
    if guild.me is None or not (channel.permissions_for(guild.me).send_messages and
                                channel.permissions_for(guild.me).embed_links):
        raise web.HTTPForbidden(text="Бот не может отправлять объявления в этот канал.")
    kind = data.get("kind")
    color = int_field(data, "color", 0, 0xFFFFFF)
    try:
        row = await announcements.create(
            bot.database, guild_id=bot.config.guild_id, author_id=actor.id, channel_id=cid,
            kind=kind, title=text_field(data, "title", 1, 120),
            body=text_field(data, "body", 1, 3500), color=color)
    except ValueError as exc:
        raise web.HTTPUnprocessableEntity(text=str(exc)) from exc
    await audit_owner(bot, actor.id, "web.announcement.created", row.id)
    return web.json_response({"ok": True, "item": announcement_card(row)})


async def announcement_action(request: web.Request) -> web.Response:
    bot, actor = await owner(request)
    require_csrf(request)
    if actor.id == 0:
        raise web.HTTPForbidden(text="Не удалось определить локального оператора Discord.")
    aid = number(request.match_info["id"])
    data = await json_payload(request)
    action = data.get("action")
    row = await announcements.get(bot.database, bot.config.guild_id, aid)
    if row is None:
        raise web.HTTPNotFound(text="Объявление не найдено.")
    try:
        if action == "cancel":
            row = await announcements.cancel(bot.database, guild_id=bot.config.guild_id, number=aid)
        elif action == "schedule":
            manager = bot.get_cog("AnnouncementManager")
            if manager is None:
                raise web.HTTPServiceUnavailable(text="Модуль объявлений не загружен.")
            seconds = int_field(data, "due_at", int(time.time()) + 60, int(time.time()) + 365*86400)
            row = await announcements.schedule(bot.database, guild_id=bot.config.guild_id,
                                               number=aid, due_at=seconds)
            await manager._enqueue(row)
        elif action == "edit":
            row = await announcements.edit(bot.database, guild_id=bot.config.guild_id,
                number=aid, actor_id=actor.id, title=text_field(data, "title", 1, 120),
                body=text_field(data, "body", 1, 3500))
        elif action == "send":
            if data.get("confirm") != "SEND":
                raise web.HTTPUnprocessableEntity(text="Подтвердите отправку объявления.")
            manager = bot.get_cog("AnnouncementManager")
            if manager is None:
                raise web.HTTPServiceUnavailable(text="Модуль объявлений не загружен.")
            result = await manager._deliver(row, scheduled_only=False)
            confirmed = result.startswith(f"Объявление #{aid} опубликовано.")
            await audit_owner(bot, actor.id, "web.announcement.sent" if confirmed
                              else "web.announcement.send_failed", aid)
            return web.json_response({"ok": confirmed, "message" if confirmed else "error": result},
                                     status=200 if confirmed else 503)
        elif action == "template":
            await announcements.save_template(
                bot.database, guild_id=bot.config.guild_id,
                slug=text_field(data, "slug", 2, 32), author_id=actor.id, number=aid)
        elif action == "duplicate":
            row = await announcements.duplicate(
                bot.database, guild_id=bot.config.guild_id, number=aid, author_id=actor.id)
        else:
            raise web.HTTPBadRequest(text="Неизвестное действие с объявлением.")
    except ValueError as exc:
        raise web.HTTPUnprocessableEntity(text=str(exc)) from exc
    await audit_owner(bot, actor.id, f"web.announcement.{action}", aid)
    return web.json_response({"ok": True, "message": "Объявление обновлено."})



async def role_rules(request: web.Request) -> web.Response:
    bot, _ = await owner(request)
    auto = await bot.database.get_setting(bot.config.guild_id, "roles_auto_role_ids", [])
    async with bot.database.sessions() as session:
        rewards = (await session.scalars(select(LevelRoleReward).where(
            LevelRoleReward.guild_id == bot.config.guild_id).order_by(LevelRoleReward.level).limit(200))).all()
    return web.json_response({"autoroles": [str(r) for r in auto], "level_rewards": [
        {"level": r.level, "role_id": str(r.role_id)} for r in rewards]})


async def role_rule_action(request: web.Request) -> web.Response:
    bot, actor = await owner(request)
    require_csrf(request)
    data = await json_payload(request)
    category = data.get("category")
    if category not in ("autorole", "level"):
        raise web.HTTPUnprocessableEntity(text="Недопустимая категория награды.")
    if type(data.get("enabled")) is not bool:
        raise web.HTTPUnprocessableEntity(text="Укажите enabled=true/false.")
    guild = guild_for(bot)
    if guild is None:
        raise web.HTTPServiceUnavailable(text="Discord недоступен.")
    role_id = identifier(data.get("role_id"))
    role = guild.get_role(role_id)
    cog = bot.get_cog("Roles")
    if data["enabled"] and (cog is None or role is None or not await cog._role_allowed(guild, role)):
        raise web.HTTPForbidden(text="Роль небезопасна или находится выше роли бота.")
    if category == "autorole":
        roles = await bot.database.get_setting(bot.config.guild_id, "roles_auto_role_ids", [])
        roles = sorted((set(roles) | {role_id}) if data["enabled"] else (set(roles) - {role_id}))
        await bot.database.set_setting(bot.config.guild_id, "roles_auto_role_ids",
                                       roles, actor_id=actor.id)
    else:
        level = int_field(data, "level", 1, 10000)
        if data["enabled"]:
            try:
                async with bot.database.session() as session:
                    session.add(LevelRoleReward(guild_id=bot.config.guild_id,
                                                level=level, role_id=role_id))
            except IntegrityError as exc:
                raise web.HTTPConflict(text="Это правило уже существует.") from exc
        else:
            async with bot.database.session() as session:
                await session.execute(delete(LevelRoleReward).where(
                    LevelRoleReward.guild_id == bot.config.guild_id,
                    LevelRoleReward.level == level, LevelRoleReward.role_id == role_id))
    await audit_owner(bot, actor.id, "web.role_rule.updated", role_id)
    return web.json_response({"ok": True, "message": "Правила ролей обновлены."})


def register(app: web.Application) -> None:
    app.router.add_get("/api/owner/shop", shop_list)
    app.router.add_post("/api/owner/shop", shop_create)
    app.router.add_post("/api/owner/shop/{id}", shop_change)
    app.router.add_get("/api/owner/role-panels", role_panels)
    app.router.add_get("/api/owner/role-rules", role_rules)
    app.router.add_post("/api/owner/role-rules", role_rule_action)
    app.router.add_post("/api/owner/role-panels", role_panel_create)
    app.router.add_post("/api/owner/role-panels/{id}/action", role_panel_action)
    app.router.add_get("/api/owner/ticket-categories", categories_list)
    app.router.add_post("/api/owner/ticket-categories", categories_create)
    app.router.add_post("/api/owner/ticket-categories/{id}", categories_change)
    app.router.add_get("/api/owner/announcements", announcement_list)
    app.router.add_post("/api/owner/announcements", announcement_create)
    app.router.add_post("/api/owner/announcements/{id}/action", announcement_action)
