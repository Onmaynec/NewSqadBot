"""Атомарная экономика NewSquad: защита от повторов и расходов в минус."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError

from app.models import CurrencyEntry, InventoryItem, ShopItem, Wallet

MAX_BALANCE = 1_000_000_000_000
MAX_OPERATION = 1_000_000
CURRENCY = "NS Coins"
ITEM_KINDS = {"title", "role"}


@dataclass(frozen=True)
class EconomyResult:
    amount: int
    balance: int
    reference: str
    repeated: bool = False
    streak: int = 0


def text_field(value: str, limit: int) -> str:
    value = " ".join(value.strip().split())
    if not value or len(value) > limit or any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValueError(f"Текст должен содержать 1–{limit} символов без управляющих символов.")
    return value


def code_field(value: str) -> str:
    code = value.strip().lower()
    if not re.fullmatch(r"[a-z0-9_-]{2,40}", code):
        raise ValueError("Код товара: 2–40 латинских букв, цифр, дефисов или подчёркиваний.")
    return code


def check_amount(amount: int, *, maximum: int = MAX_OPERATION) -> None:
    if isinstance(amount, bool) or not isinstance(amount, int) or not 1 <= amount <= maximum:
        raise ValueError(f"Сумма должна быть целым числом от 1 до {maximum:,}.")


def local_day(timestamp: int, timezone: str) -> date:
    return datetime.fromtimestamp(timestamp, ZoneInfo(timezone)).date()


async def _ensure_wallet(session, guild_id: int, user_id: int, now: int) -> None:
    stmt = sqlite_insert(Wallet).values(
        guild_id=guild_id, user_id=user_id, balance=0, earned=0, spent=0,
        last_daily="", daily_streak=0, created_at=now, updated_at=now,
    ).on_conflict_do_nothing(index_elements=[Wallet.guild_id, Wallet.user_id])
    await session.execute(stmt)


async def _apply(session, *, guild_id: int, user_id: int, amount: int,
                 kind: str, reference: str, now: int, actor_id: int | None) -> EconomyResult:
    if not amount or abs(amount) > MAX_OPERATION:
        raise ValueError("Некорректная сумма операции.")
    if not reference or len(reference) > 180:
        raise ValueError("Некорректный ключ операции.")
    await _ensure_wallet(session, guild_id, user_id, now)
    receipt = sqlite_insert(CurrencyEntry).values(
        guild_id=guild_id, user_id=user_id, amount=amount, kind=kind,
        reference=reference, actor_id=actor_id, created_at=now,
    ).on_conflict_do_nothing(index_elements=[CurrencyEntry.guild_id, CurrencyEntry.reference])
    inserted = await session.execute(receipt)
    if inserted.rowcount != 1:
        old = await session.scalar(select(CurrencyEntry).where(
            CurrencyEntry.guild_id == guild_id, CurrencyEntry.reference == reference))
        if old is None or old.user_id != user_id or old.amount != amount or old.kind != kind:
            raise ValueError("Ключ операции уже использован для другого действия.")
        balance = await session.scalar(select(Wallet.balance).where(
            Wallet.guild_id == guild_id, Wallet.user_id == user_id))
        return EconomyResult(0, balance or 0, reference, repeated=True)
    stmt = (update(Wallet).where(Wallet.guild_id == guild_id, Wallet.user_id == user_id,
                                 Wallet.balance + amount >= 0,
                                 Wallet.balance + amount <= MAX_BALANCE)
            .values(balance=Wallet.balance + amount,
                    earned=Wallet.earned + max(0, amount),
                    spent=Wallet.spent + max(0, -amount), updated_at=now))
    changed = await session.execute(stmt)
    if changed.rowcount != 1:
        raise ValueError("Недостаточно средств либо превышен лимит кошелька.")
    balance = await session.scalar(select(Wallet.balance).where(
        Wallet.guild_id == guild_id, Wallet.user_id == user_id))
    return EconomyResult(amount, balance, reference)


async def wallet(db, guild_id: int, user_id: int) -> Wallet | None:
    async with db.sessions() as session:
        return await session.scalar(select(Wallet).where(Wallet.guild_id == guild_id, Wallet.user_id == user_id))


async def top(db, guild_id: int, limit: int = 20) -> list[Wallet]:
    async with db.sessions() as session:
        return list((await session.scalars(select(Wallet).where(
            Wallet.guild_id == guild_id, Wallet.balance > 0
        ).order_by(Wallet.balance.desc(), Wallet.user_id).limit(limit))).all())


async def ledger(db, guild_id: int, user_id: int, limit: int = 10) -> list[CurrencyEntry]:
    async with db.sessions() as session:
        return list((await session.scalars(select(CurrencyEntry).where(
            CurrencyEntry.guild_id == guild_id, CurrencyEntry.user_id == user_id
        ).order_by(CurrencyEntry.id.desc()).limit(limit))).all())


async def credit_once(db, *, guild_id: int, user_id: int, amount: int,
                      kind: str, reference: str, actor_id: int | None = None,
                      now: int | None = None) -> EconomyResult:
    check_amount(amount)
    now = now if now is not None else int(time.time())
    async with db.session() as session:
        return await _apply(session, guild_id=guild_id, user_id=user_id, amount=amount,
                            kind=kind, reference=reference, actor_id=actor_id, now=now)


async def adjust(db, *, guild_id: int, user_id: int, delta: int, actor_id: int,
                 reference: str) -> EconomyResult:
    check_amount(abs(delta))
    async with db.session() as session:
        return await _apply(session, guild_id=guild_id, user_id=user_id, amount=delta,
                            kind="admin", reference=reference, actor_id=actor_id, now=int(time.time()))


async def transfer(db, *, guild_id: int, sender: int, recipient: int, amount: int,
                   reference: str) -> EconomyResult:
    check_amount(amount, maximum=100_000)
    if sender == recipient:
        raise ValueError("Нельзя отправлять валюту самому себе.")
    now = int(time.time())
    async with db.session() as session:
        debit = await _apply(session, guild_id=guild_id, user_id=sender, amount=-amount,
                             kind="transfer_out", reference=f"{reference}:out", actor_id=sender, now=now)
        if debit.repeated:
            # Complete transfers always commit in one DB transaction.
            other = await session.scalar(select(CurrencyEntry).where(
                CurrencyEntry.guild_id == guild_id, CurrencyEntry.reference == f"{reference}:in",
                CurrencyEntry.user_id == recipient, CurrencyEntry.amount == amount))
            if other is None:
                raise ValueError("Ключ перевода уже использован.")
            return debit
        await _apply(session, guild_id=guild_id, user_id=recipient, amount=amount,
                     kind="transfer_in", reference=f"{reference}:in", actor_id=sender, now=now)
        return debit


async def claim_daily(db, *, guild_id: int, user_id: int, timezone: str,
                      base: int = 100, now: int | None = None) -> EconomyResult:
    check_amount(base, maximum=1000)
    now = int(time.time()) if now is None else now
    today = local_day(now, timezone)
    async with db.session() as session:
        await _ensure_wallet(session, guild_id, user_id, now)
        row = await session.scalar(select(Wallet).where(Wallet.guild_id == guild_id, Wallet.user_id == user_id))
        if row.last_daily == today.isoformat():
            raise ValueError("Сегодня награда уже получена. Возвращайтесь завтра.")
        previous = date.fromisoformat(row.last_daily) if row.last_daily else None
        streak = min(row.daily_streak + 1, 7) if previous == today - timedelta(days=1) else 1
        amount = base + (streak - 1) * 10
        result = await _apply(session, guild_id=guild_id, user_id=user_id, amount=amount,
                              kind="daily", reference=f"daily:{user_id}:{today.isoformat()}",
                              actor_id=user_id, now=now)
        if result.repeated:
            raise ValueError("Награда за этот день уже начислена.")
        row.last_daily = today.isoformat()
        row.daily_streak = streak
        return EconomyResult(amount, result.balance, result.reference, streak=streak)


async def add_item(db, *, guild_id: int, code: str, title: str, kind: str,
                   price: int, description: str = "", role_id: int | None = None,
                   stock: int | None = None) -> ShopItem:
    code = code_field(code)
    title = text_field(title, 80)
    description = text_field(description, 300) if description.strip() else ""
    if kind not in ITEM_KINDS or (kind == "role") != (role_id is not None):
        raise ValueError("Тип должен быть title или role; для role требуется ID роли.")
    check_amount(price)
    if stock is not None:
        check_amount(stock)
    now = int(time.time())
    try:
        async with db.session() as session:
            item = ShopItem(guild_id=guild_id, code=code, title=title, kind=kind,
                            description=description, price=price, role_id=role_id,
                            stock=stock, sold=0, active=True, created_at=now)
            session.add(item)
            await session.flush()
            return item
    except IntegrityError as exc:
        raise ValueError("Товар с таким кодом уже существует.") from exc


async def items(db, guild_id: int, *, active_only: bool = True) -> list[ShopItem]:
    async with db.sessions() as session:
        q = select(ShopItem).where(ShopItem.guild_id == guild_id)
        if active_only:
            q = q.where(ShopItem.active.is_(True))
        return list((await session.scalars(q.order_by(ShopItem.id).limit(100))).all())


async def item_by_id(db, guild_id: int, item_id: int) -> ShopItem | None:
    async with db.sessions() as session:
        return await session.scalar(select(ShopItem).where(
            ShopItem.guild_id == guild_id, ShopItem.id == item_id))


async def set_item_active(db, guild_id: int, item_id: int, active: bool) -> bool:
    async with db.session() as session:
        changed = await session.execute(update(ShopItem).where(
            ShopItem.guild_id == guild_id, ShopItem.id == item_id).values(active=active))
        return changed.rowcount == 1


async def inventory(db, guild_id: int, user_id: int) -> list[tuple[InventoryItem, ShopItem]]:
    async with db.sessions() as session:
        rows = (await session.execute(select(InventoryItem, ShopItem).join(
            ShopItem, ShopItem.id == InventoryItem.item_id).where(
                InventoryItem.guild_id == guild_id, InventoryItem.user_id == user_id,
                ShopItem.guild_id == guild_id).order_by(InventoryItem.id))).all()
        return list(rows)


async def purchase(db, *, guild_id: int, user_id: int, item_id: int) -> InventoryItem:
    now = int(time.time())
    try:
        async with db.session() as session:
            item = await session.scalar(select(ShopItem).where(
                ShopItem.guild_id == guild_id, ShopItem.id == item_id, ShopItem.active.is_(True)))
            if item is None:
                raise ValueError("Товар не найден или снят с продажи.")
            existing = await session.scalar(select(InventoryItem).where(
                InventoryItem.guild_id == guild_id, InventoryItem.user_id == user_id,
                InventoryItem.item_id == item_id))
            if existing:
                raise ValueError("Вы уже приобрели этот товар.")
            stock_change = await session.execute(update(ShopItem).where(
                ShopItem.id == item_id, ShopItem.guild_id == guild_id,
                ShopItem.active.is_(True), or_(ShopItem.stock.is_(None), ShopItem.sold < ShopItem.stock)
            ).values(sold=ShopItem.sold + 1))
            if stock_change.rowcount != 1:
                raise ValueError("Товар закончился.")
            await _apply(session, guild_id=guild_id, user_id=user_id, amount=-item.price,
                         kind="purchase", reference=f"buy:{user_id}:{item_id}",
                         actor_id=user_id, now=now)
            record = InventoryItem(guild_id=guild_id, user_id=user_id, item_id=item_id,
                                   equipped=False, delivery="pending" if item.kind == "role" else "not_required",
                                   purchased_at=now)
            session.add(record)
            await session.flush()
            return record
    except IntegrityError as exc:
        raise ValueError("Товар уже приобретён или операция покупки конфликтует с другой.") from exc


async def equip_title(db, guild_id: int, user_id: int, item_id: int | None) -> None:
    async with db.session() as session:
        if item_id is not None:
            owned = await session.scalar(select(InventoryItem.id).join(
                ShopItem, ShopItem.id == InventoryItem.item_id).where(
                InventoryItem.guild_id == guild_id, InventoryItem.user_id == user_id,
                ShopItem.guild_id == guild_id, ShopItem.kind == "title", InventoryItem.item_id == item_id))
            if owned is None:
                raise ValueError("Титул не приобретён.")
        await session.execute(update(InventoryItem).where(
            InventoryItem.guild_id == guild_id, InventoryItem.user_id == user_id
        ).values(equipped=False))
        if item_id is not None:
            await session.execute(update(InventoryItem).where(
                InventoryItem.guild_id == guild_id, InventoryItem.user_id == user_id,
                InventoryItem.item_id == item_id).values(equipped=True))


async def equipped_title(db, guild_id: int, user_id: int) -> str | None:
    async with db.sessions() as session:
        return await session.scalar(select(ShopItem.title).join(
            InventoryItem, InventoryItem.item_id == ShopItem.id).where(
            InventoryItem.guild_id == guild_id, InventoryItem.user_id == user_id,
            ShopItem.guild_id == guild_id, ShopItem.kind == "title", InventoryItem.equipped.is_(True)))


async def mark_role_delivered(db, guild_id: int, user_id: int, item_id: int) -> None:
    async with db.session() as session:
        await session.execute(update(InventoryItem).where(
            InventoryItem.guild_id == guild_id, InventoryItem.user_id == user_id,
            InventoryItem.item_id == item_id, InventoryItem.delivery == "pending"
        ).values(delivery="delivered"))


async def pending_roles(db, guild_id: int, user_id: int | None = None) -> list[tuple[InventoryItem, ShopItem]]:
    async with db.sessions() as session:
        q = select(InventoryItem, ShopItem).join(ShopItem, ShopItem.id == InventoryItem.item_id).where(
            InventoryItem.guild_id == guild_id, ShopItem.guild_id == guild_id,
            ShopItem.kind == "role", InventoryItem.delivery == "pending")
        if user_id is not None:
            q = q.where(InventoryItem.user_id == user_id)
        return list((await session.execute(q.limit(100))).all())
