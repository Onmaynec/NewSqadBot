"""Real-currency order intake: server-side prices, atomic stock and auditable states.

No card data, payment secrets, Nitro codes or Discord credentials live here.
A buyer's "paid" button is a report, not a verified financial transaction.
"""
from __future__ import annotations

import re
import time
from decimal import Decimal, InvalidOperation

from sqlalchemy import case, func, or_, select, text, update
from sqlalchemy.exc import IntegrityError

from app.models import StoreOrder, StoreOrderEvent, StorePanel, StoreProduct

SKU = re.compile(r"[a-z0-9][a-z0-9_-]{1,39}\Z")
CURRENCIES = {"RUB": "₽", "EUR": "€", "USD": "$"}
CATEGORIES = {"nitro_gift", "server_boost", "digital", "service", "other"}
OPEN_STATES = ("awaiting_payment", "payment_reported", "processing")
STATES = (*OPEN_STATES, "completed", "cancelled")
NEXT = {
    "report_payment": (("awaiting_payment",), "payment_reported"),
    "confirm_payment": (("awaiting_payment", "payment_reported"), "processing"),
    "complete": (("processing",), "completed"),
    "cancel": (OPEN_STATES, "cancelled"),
}


def parse_price(value: str) -> int:
    """Convert 12.50 to integer 1250; reject floats, exponent and negatives."""
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,7}(?:[.,][0-9]{1,2})?", value):
        raise ValueError("Цена: например 399 или 399.50, без знака валюты.")
    try:
        price = Decimal(value.replace(",", "."))
        minor = int(price * 100)
    except (InvalidOperation, OverflowError) as exc:
        raise ValueError("Некорректная цена.") from exc
    if not 1 <= minor <= 999_999_999:
        raise ValueError("Цена должна быть больше нуля и не превышать 9 999 999,99.")
    return minor


def format_money(minor: int, currency: str) -> str:
    units, cents = divmod(minor, 100)
    return f"{units:,}".replace(",", " ") + f",{cents:02d} " + CURRENCIES.get(currency, currency)


def validate_product(*, sku: str, title: str, description: str,
                     category: str, price_minor: int, currency: str,
                     stock: int | None, active: bool, sort_order: int = 0) -> dict:
    if not isinstance(sku, str) or not SKU.fullmatch(sku):
        raise ValueError("Артикул: 2–40 строчных латинских символов, цифр, - или _.")
    if not isinstance(title, str) or not 3 <= len(title.strip()) <= 90:
        raise ValueError("Название: от 3 до 90 символов.")
    if not isinstance(description, str) or len(description.strip()) > 600:
        raise ValueError("Описание: максимум 600 символов.")
    if category not in CATEGORIES:
        raise ValueError("Категория товара не поддерживается.")
    if currency not in CURRENCIES:
        raise ValueError("Валюта: RUB, EUR или USD.")
    if type(price_minor) is not int or not 1 <= price_minor <= 999_999_999:
        raise ValueError("Цена должна быть указана в минимальных единицах валюты.")
    if stock is not None and (type(stock) is not int or not 0 <= stock <= 1_000_000):
        raise ValueError("Остаток: от 0 до 1 000 000 либо безлимит.")
    if type(active) is not bool or type(sort_order) is not int or not -999 <= sort_order <= 999:
        raise ValueError("Неверные настройки карточки.")
    return dict(sku=sku, title=title.strip(), description=description.strip(),
                category=category, price_minor=price_minor, currency=currency,
                stock=stock, active=active, sort_order=sort_order)


async def save_product(db, guild_id: int, *, sku: str, title: str,
                       description: str = "", category: str = "digital",
                       price_minor: int, currency: str = "RUB",
                       stock: int | None = None, active: bool = True,
                       sort_order: int = 0) -> StoreProduct:
    validated = validate_product(sku=sku, title=title, description=description,
        category=category, price_minor=price_minor, currency=currency, stock=stock,
        active=active, sort_order=sort_order)
    now = int(time.time())
    async with db.session() as session:
        # Obtain SQLite's single-writer lease before reading stock for an edit.
        await session.execute(text("BEGIN IMMEDIATE"))
        row = await session.scalar(select(StoreProduct).where(
            StoreProduct.guild_id == guild_id, StoreProduct.sku == sku))
        if row is None:
            count = await session.scalar(select(func.count()).select_from(StoreProduct).where(
                StoreProduct.guild_id == guild_id))
            if count >= 200:
                raise ValueError("Лимит каталога: не более 200 товаров.")
            row = StoreProduct(guild_id=guild_id, **validated,
                               created_at=now, updated_at=now)
            session.add(row)
        else:
            for key, value in validated.items():
                setattr(row, key, value)
            row.updated_at = now
        await session.flush()
        return row


async def products(db, guild_id: int, *, public: bool = False, limit: int = 200) -> list[StoreProduct]:
    async with db.sessions() as session:
        query = select(StoreProduct).where(StoreProduct.guild_id == guild_id)
        if public:
            query = query.where(StoreProduct.active.is_(True),
                                or_(StoreProduct.stock.is_(None), StoreProduct.stock > 0))
        return list((await session.scalars(query.order_by(
            StoreProduct.sort_order, StoreProduct.id).limit(min(max(limit, 1), 200)))).all())


async def product_by_id(db, guild_id: int, product_id: int) -> StoreProduct | None:
    async with db.sessions() as session:
        return await session.scalar(select(StoreProduct).where(
            StoreProduct.id == product_id, StoreProduct.guild_id == guild_id))


async def product_by_sku(db, guild_id: int, sku: str) -> StoreProduct | None:
    async with db.sessions() as session:
        return await session.scalar(select(StoreProduct).where(
            StoreProduct.guild_id == guild_id, StoreProduct.sku == sku))


async def order_by_id(db, guild_id: int, order_id: int) -> StoreOrder | None:
    async with db.sessions() as session:
        return await session.scalar(select(StoreOrder).where(
            StoreOrder.id == order_id, StoreOrder.guild_id == guild_id))


async def make_order(db, guild_id: int, user_id: int, product_id: int,
                     interaction_id: int, *, quantity: int = 1,
                     expected_price: int | None = None) -> tuple[StoreOrder, bool]:
    """Use conditional UPDATE to reserve stock and a unique interaction for idempotency.

    Returns (order, created), so a retried Discord interaction never oversells.
    """
    if any(type(v) is not int or v <= 0 for v in (guild_id, user_id, product_id, interaction_id)):
        raise ValueError("Неверный идентификатор.")
    if type(quantity) is not int or not 1 <= quantity <= 10:
        raise ValueError("За один заказ можно купить от 1 до 10 единиц.")
    try:
        async with db.session() as session:
            # Avoid WAL snapshot-upgrade races when two buyers take the final unit.
            # busy_timeout is configured by Database; other writers wait.
            await session.execute(text("BEGIN IMMEDIATE"))
            prior = await session.scalar(select(StoreOrder).where(
                StoreOrder.guild_id == guild_id, StoreOrder.interaction_id == interaction_id))
            if prior is not None:
                return prior, False
            # SQLite serializes writers; the stock UPDATE is conditional in the same transaction.
            count = await session.scalar(select(func.count()).select_from(StoreOrder).where(
                StoreOrder.guild_id == guild_id, StoreOrder.user_id == user_id,
                StoreOrder.status.in_(OPEN_STATES)))
            if count >= 3:
                raise ValueError("Не более трёх незавершённых заказов. Дождитесь ответа продавца.")
            item = await session.scalar(select(StoreProduct).where(
                StoreProduct.id == product_id, StoreProduct.guild_id == guild_id))
            if item is None or not item.active:
                raise ValueError("Товар больше недоступен.")
            if expected_price is not None and item.price_minor != expected_price:
                raise ValueError("Цена товара изменилась. Откройте каталог и подтвердите новую цену.")
            total = item.price_minor * quantity
            if total > 999_999_999:
                raise ValueError("Сумма заказа превышает допустимый лимит.")
            result = await session.execute(update(StoreProduct).where(
                StoreProduct.id == product_id, StoreProduct.guild_id == guild_id,
                StoreProduct.active.is_(True),
                or_(StoreProduct.stock.is_(None), StoreProduct.stock >= quantity),
            ).values(stock=case(
                (StoreProduct.stock.is_(None), None),
                else_=StoreProduct.stock - quantity,
            )).execution_options(synchronize_session=False))
            if result.rowcount != 1:
                raise ValueError("Товар закончился. Обновите каталог.")
            now = int(time.time())
            order = StoreOrder(
                guild_id=guild_id, user_id=user_id, product_id=product_id,
                interaction_id=interaction_id, sku=item.sku, product_title=item.title,
                quantity=quantity, price_minor=total, currency=item.currency,
                status="awaiting_payment", created_at=now, updated_at=now)
            session.add(order)
            await session.flush()
            session.add(StoreOrderEvent(order_id=order.id, actor_id=user_id,
                old_status="", new_status=order.status, created_at=now))
            return order, True
    except IntegrityError:
        # Unique interaction_id protection also covers races and process retries.
        async with db.sessions() as session:
            previous = await session.scalar(select(StoreOrder).where(
                StoreOrder.guild_id == guild_id, StoreOrder.interaction_id == interaction_id))
            if previous is not None:
                return previous, False
        raise


async def transition(db, guild_id: int, order_id: int, *, actor_id: int,
                     action: str, staff: bool = False,
                     owner_id: int | None = None) -> StoreOrder | None:
    if action not in NEXT or actor_id <= 0:
        raise ValueError("Неизвестное действие над заказом.")
    if action not in ("report_payment", "cancel") and not staff:
        raise PermissionError("Изменить статус может только продавец.")
    before, after = NEXT[action]
    async with db.session() as session:
        await session.execute(text("BEGIN IMMEDIATE"))
        row = await session.scalar(select(StoreOrder).where(
            StoreOrder.id == order_id, StoreOrder.guild_id == guild_id))
        if row is None:
            return None
        if not staff and (row.user_id != actor_id or owner_id != actor_id):
            raise PermissionError("Это заказ другого участника.")
        if row.status not in before:
            return None
        if not staff and action == "cancel" and row.status == "processing":
            raise PermissionError("Оплата уже проверена. Для отмены обратитесь к продавцу.")
        now = int(time.time())
        result = await session.execute(update(StoreOrder).where(
            StoreOrder.id == order_id, StoreOrder.guild_id == guild_id,
            StoreOrder.status == row.status,
        ).values(status=after, updated_at=now,
                 completed_at=now if after in ("completed", "cancelled") else None)
         .execution_options(synchronize_session=False))
        if result.rowcount != 1:
            return None
        if after == "cancelled":
            await session.execute(update(StoreProduct).where(
                StoreProduct.id == row.product_id,
                StoreProduct.stock.is_not(None),
            ).values(stock=StoreProduct.stock + row.quantity))
        session.add(StoreOrderEvent(order_id=order_id, actor_id=actor_id,
            old_status=row.status, new_status=after, created_at=now))
    return await order_by_id(db, guild_id, order_id)


async def orders(db, guild_id: int, *, user_id: int | None = None,
                 status: str | None = None, limit: int = 50) -> list[StoreOrder]:
    if status is not None and status not in STATES:
        raise ValueError("Неизвестный статус заказа.")
    async with db.sessions() as session:
        stmt = select(StoreOrder).where(StoreOrder.guild_id == guild_id)
        if user_id is not None:
            stmt = stmt.where(StoreOrder.user_id == user_id)
        if status is not None:
            stmt = stmt.where(StoreOrder.status == status)
        return list((await session.scalars(stmt.order_by(
            StoreOrder.id.desc()).limit(max(1, min(limit, 200))))).all())


async def events(db, guild_id: int, order_id: int) -> list[StoreOrderEvent]:
    if await order_by_id(db, guild_id, order_id) is None:
        return []
    async with db.sessions() as session:
        return list((await session.scalars(select(StoreOrderEvent).where(
            StoreOrderEvent.order_id == order_id).order_by(StoreOrderEvent.id).limit(50))).all())


async def set_staff_message(db, guild_id: int, order_id: int,
                            channel_id: int, message_id: int) -> bool:
    async with db.session() as session:
        result = await session.execute(update(StoreOrder).where(
            StoreOrder.guild_id == guild_id, StoreOrder.id == order_id,
            StoreOrder.staff_message_id.is_(None),
        ).values(staff_channel_id=channel_id, staff_message_id=message_id))
        return result.rowcount == 1


async def register_panel(db, guild_id: int, channel_id: int, message_id: int,
                         created_by: int) -> None:
    async with db.session() as session:
        session.add(StorePanel(guild_id=guild_id, channel_id=channel_id,
            message_id=message_id, created_by=created_by, created_at=int(time.time())))
