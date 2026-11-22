"""Local-only owner API for the seller storefront; payment execution is external."""
from __future__ import annotations

from aiohttp import web
from sqlalchemy import func, select

from app.models import StoreOrder
from app.services import store as service
from app.web.server import json_payload, require_csrf, require_role, state

API_VERSION = "1.0"


async def auth(request: web.Request, *, mutation: bool = False):
    role, member = await require_role(request, "owner")
    if role != "owner":
        raise web.HTTPForbidden(text="Доступ к магазину ограничен владельцем.")
    if mutation:
        require_csrf(request)
    bot = state(request).bot
    operator = member.id if member is not None else (
        getattr(bot.config, "web_local_operator_id", 0)
        or getattr(bot.get_guild(bot.config.guild_id), "owner_id", 0))
    return bot, operator


def identifier(raw: str) -> int:
    if not isinstance(raw, str) or not raw.isascii() or not raw.isdecimal():
        raise web.HTTPBadRequest(text="Некорректный номер заказа.")
    number = int(raw)
    if not 0 < number <= 2**63 - 1:
        raise web.HTTPBadRequest(text="Некорректный номер заказа.")
    return number


def product_public(row) -> dict:
    return {
        "id": row.id, "sku": row.sku, "title": row.title,
        "description": row.description, "category": row.category,
        "price_minor": row.price_minor, "currency": row.currency,
        "stock": row.stock, "active": row.active, "sort_order": row.sort_order,
        "created_at": row.created_at, "updated_at": row.updated_at,
    }


def order_public(row) -> dict:
    return {
        "id": row.id, "user_id": str(row.user_id), "product_id": row.product_id,
        "sku": row.sku, "product_title": row.product_title,
        "quantity": row.quantity, "price_minor": row.price_minor,
        "currency": row.currency, "status": row.status,
        "created_at": row.created_at, "updated_at": row.updated_at,
        "completed_at": row.completed_at,
    }


async def products_get(request: web.Request) -> web.Response:
    bot, _ = await auth(request)
    rows = await service.products(bot.database, bot.config.guild_id)
    return web.json_response({"items": [product_public(row) for row in rows],
                              "contract": API_VERSION})


async def product_save(request: web.Request) -> web.Response:
    bot, _ = await auth(request, mutation=True)
    value = await json_payload(request)
    allowed = {"sku", "title", "description", "category", "price_minor",
               "currency", "stock", "active", "sort_order"}
    if set(value) - allowed:
        raise web.HTTPUnprocessableEntity(text="Некорректные поля товара.")
    try:
        row = await service.save_product(bot.database, bot.config.guild_id,
            sku=value.get("sku"), title=value.get("title"),
            description=value.get("description", ""), category=value.get("category", "digital"),
            price_minor=value.get("price_minor"), currency=value.get("currency", "RUB"),
            stock=value.get("stock"), active=value.get("active", True),
            sort_order=value.get("sort_order", 0))
    except (ValueError, TypeError) as exc:
        raise web.HTTPUnprocessableEntity(text=str(exc)) from exc
    return web.json_response({"item": product_public(row)})


async def orders_get(request: web.Request) -> web.Response:
    bot, _ = await auth(request)
    status = request.query.get("status") or None
    try:
        rows = await service.orders(bot.database, bot.config.guild_id, status=status, limit=100)
    except ValueError as exc:
        raise web.HTTPBadRequest(text=str(exc)) from exc
    return web.json_response({"items": [order_public(row) for row in rows],
                              "contract": API_VERSION})


async def order_detail(request: web.Request) -> web.Response:
    bot, _ = await auth(request)
    num = identifier(request.match_info["id"])
    row = await service.order_by_id(bot.database, bot.config.guild_id, num)
    if row is None:
        raise web.HTTPNotFound(text="Заказ не найден.")
    events = await service.events(bot.database, bot.config.guild_id, num)
    return web.json_response({"order": order_public(row),
        "history": [{
            "from": step.old_status, "to": step.new_status,
            "actor_id": str(step.actor_id), "at": step.created_at
        } for step in events[:50]]})


async def order_action(request: web.Request) -> web.Response:
    bot, actor = await auth(request, mutation=True)
    if not actor:
        raise web.HTTPServiceUnavailable(text="Не определён оператор для журнала действий.")
    num = identifier(request.match_info["id"])
    value = await json_payload(request)
    action = value.get("action")
    if action not in ("confirm_payment", "complete", "cancel") or set(value) != {"action"}:
        raise web.HTTPUnprocessableEntity(text="Разрешены confirm_payment, complete, cancel.")
    order = await service.transition(bot.database, bot.config.guild_id, num,
                                     actor_id=actor, action=action, staff=True)
    if order is None:
        raise web.HTTPConflict(text="Заказ не найден или переход статуса невозможен.")
    cog = bot.get_cog("Store") if hasattr(bot, "get_cog") else None
    if cog:
        await cog.sync_staff(order)
        await cog.notify_buyer(order)
    return web.json_response({"order": order_public(order)})


async def settings_get(request: web.Request) -> web.Response:
    bot, _ = await auth(request)
    return web.json_response({
        "staff_channel_id": str(await bot.database.get_setting(
            bot.config.guild_id, "store_staff_channel_id", "") or ""),
        "payment_instructions": await bot.database.get_setting(
            bot.config.guild_id, "store_payment_instructions", ""),
        "contract": API_VERSION,
    })


async def settings_save(request: web.Request) -> web.Response:
    bot, actor = await auth(request, mutation=True)
    if not actor:
        raise web.HTTPServiceUnavailable(text="Не определён оператор.")
    value = await json_payload(request)
    if set(value) != {"staff_channel_id", "payment_instructions"}:
        raise web.HTTPUnprocessableEntity(text="Требуются канал и инструкция оплаты.")
    note, raw_id = value["payment_instructions"], value["staff_channel_id"]
    if not isinstance(note, str) or not 20 <= len(note.strip()) <= 650:
        raise web.HTTPUnprocessableEntity(text="Инструкция оплаты: от 20 до 650 символов.")
    if not isinstance(raw_id, str) or not raw_id.isascii() or not raw_id.isdecimal():
        raise web.HTTPUnprocessableEntity(text="Укажите Discord ID приватного канала.")
    channel_id = int(raw_id)
    if channel_id <= 0 or channel_id >= 2**64:
        raise web.HTTPUnprocessableEntity(text="Неверный ID Discord-канала.")
    guild = bot.get_guild(bot.config.guild_id)
    channel = guild.get_channel(channel_id) if guild else None
    import discord

    if not isinstance(channel, discord.TextChannel):
        raise web.HTTPUnprocessableEntity(text="Канал не найден в Discord. Выберите существующий текстовый канал.")
    if channel.permissions_for(guild.default_role).view_channel:
        raise web.HTTPUnprocessableEntity(text="Канал должен быть невидим для @everyone.")
    bot_member = guild.me
    permissions = channel.permissions_for(bot_member) if bot_member is not None else None
    if not permissions or not (permissions.view_channel and permissions.send_messages and permissions.embed_links):
        raise web.HTTPUnprocessableEntity(text="У бота нет прав на чтение, отправку сообщений и embeds.")
    await bot.database.set_setting(bot.config.guild_id, "store_staff_channel_id",
                                   channel_id, actor_id=actor)
    await bot.database.set_setting(bot.config.guild_id, "store_payment_instructions",
                                   note.strip(), actor_id=actor)
    return web.json_response({"ok": True})


async def analytics(request: web.Request) -> web.Response:
    bot, _ = await auth(request)
    async with bot.database.sessions() as session:
        status_rows = (await session.execute(select(
            StoreOrder.status, func.count(StoreOrder.id)).where(
            StoreOrder.guild_id == bot.config.guild_id).group_by(StoreOrder.status))).all()
        revenue = (await session.execute(select(
            StoreOrder.currency, func.sum(StoreOrder.price_minor)).where(
            StoreOrder.guild_id == bot.config.guild_id,
            StoreOrder.status == "completed").group_by(StoreOrder.currency))).all()
    return web.json_response({
        "counts": {name: count for name, count in status_rows},
        "completed_minor_by_currency": {code: amount for code, amount in revenue},
        "note": "Суммы отмеченных выполненными заказов, не подтверждённая выручка платёжного провайдера.",
    })


def register(app: web.Application) -> None:
    app.router.add_get("/api/v1/store/products", products_get)
    app.router.add_post("/api/v1/store/products", product_save)
    app.router.add_get("/api/v1/store/orders", orders_get)
    app.router.add_get("/api/v1/store/orders/{id}", order_detail)
    app.router.add_post("/api/v1/store/orders/{id}/action", order_action)
    app.router.add_get("/api/v1/store/settings", settings_get)
    app.router.add_post("/api/v1/store/settings", settings_save)
    app.router.add_get("/api/v1/store/analytics", analytics)
