"""v2.2.0 private store invariants: migration, money, stock, idempotency and privacy."""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig

from app.config import GUILD_ID
from app.core.database import Database, migrate
from app.services import store


@pytest.fixture
def store_url(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'store.db'}"
    migrate(url)
    return url


def test_0018_upgrade_keeps_old_settings(tmp_path):
    filename = tmp_path / "upgrade.db"
    url = f"sqlite+aiosqlite:///{filename}"
    cfg = AlembicConfig(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url.replace("sqlite+aiosqlite", "sqlite", 1))
    command.upgrade(cfg, "0017_member_support_voice")
    with sqlite3.connect(filename) as db:
        db.execute("INSERT INTO guild_settings(guild_id,key,value_json) VALUES(?,?,?)",
                   (GUILD_ID, "store_upgrade_marker", '"preserved"'))
    migrate(url)
    migrate(url)
    with sqlite3.connect(filename) as db:
        head = db.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        marker = db.execute("SELECT value_json FROM guild_settings WHERE key='store_upgrade_marker'").fetchone()[0]
    assert head == "0018_private_store"
    assert marker == '"preserved"'
    assert {"store_products", "store_orders", "store_order_events", "store_panels",
            "economy_shop_items", "tickets", "membership_applications"} <= names


def test_prices_and_card_validation():
    assert store.parse_price("199") == 19900
    assert store.parse_price("199.99") == 19999
    assert store.parse_price("10,50") == 1050
    assert store.format_money(19999, "RUB") == "199,99 ₽"
    for bad in ("0", "-1", "1e8", "1.234", "12x", "00123456789", "", "1,00.0"):
        with pytest.raises(ValueError):
            store.parse_price(bad)
    with pytest.raises(ValueError):
        store.validate_product(sku="x", title="Title", description="", category="digital",
                               price_minor=100, currency="RUB", stock=0, active=True)


@pytest.mark.asyncio
async def test_idempotent_checkout_stock_and_cancellation(store_url):
    db = Database(store_url)
    try:
        product = await store.save_product(db, GUILD_ID, sku="nitro_gift",
            title="Discord Nitro, подарочный", category="nitro_gift",
            description="Ручная передача подарка", currency="RUB", price_minor=39900, stock=1)
        order, created = await store.make_order(db, GUILD_ID, 110, product.id, 909091)
        assert created and order.price_minor == 39900
        same, created = await store.make_order(db, GUILD_ID, 110, product.id, 909091)
        assert not created and same.id == order.id
        assert (await store.product_by_id(db, GUILD_ID, product.id)).stock == 0
        with pytest.raises(ValueError):
            await store.make_order(db, GUILD_ID, 111, product.id, 909092)
        assert (await store.products(db, GUILD_ID, public=True)) == []
        with pytest.raises(PermissionError):
            await store.transition(db, GUILD_ID, order.id,
                                   actor_id=111, owner_id=111, action="cancel")
        with pytest.raises(PermissionError):
            await store.transition(db, GUILD_ID, order.id,
                                   actor_id=110, owner_id=110, action="complete")
        order = await store.transition(db, GUILD_ID, order.id,
                                       actor_id=110, owner_id=110, action="cancel")
        assert order.status == "cancelled"
        assert await store.transition(db, GUILD_ID, order.id,
                                      actor_id=110, owner_id=110, action="cancel") is None
        assert (await store.product_by_id(db, GUILD_ID, product.id)).stock == 1
        assert len(await store.events(db, GUILD_ID, order.id)) == 2
        assert (await store.orders(db, 1234)) == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_confirmed_payment_and_price_snapshot(store_url):
    db = Database(store_url)
    try:
        product = await store.save_product(db, GUILD_ID, sku="boosts_2",
            title="Два буста сервера", category="server_boost",
            description="Ручная услуга", price_minor=9900, stock=None)
        order, _ = await store.make_order(db, GUILD_ID, 200, product.id, 101,
                                          quantity=2, expected_price=9900)
        assert order.price_minor == 19800 and order.currency == "RUB"
        await store.save_product(db, GUILD_ID, sku="boosts_2", title="Два буста сервера",
            category="server_boost", description="Ручная услуга", price_minor=11900)
        assert (await store.order_by_id(db, GUILD_ID, order.id)).price_minor == 19800
        with pytest.raises(ValueError, match="Цена или валюта товара изменилась"):
            await store.make_order(db, GUILD_ID, 201, product.id, 102,
                                   expected_price=9900)
        with pytest.raises(ValueError, match="Цена или валюта товара изменилась"):
            await store.make_order(db, GUILD_ID, 202, product.id, 103,
                                   expected_price=11900, expected_currency="EUR")
        assert await store.transition(db, GUILD_ID, order.id, actor_id=200,
                                       owner_id=200, action="report_payment")
        assert await store.transition(db, GUILD_ID, order.id, actor_id=200,
                                       owner_id=200, action="report_payment") is None
        with pytest.raises(PermissionError):
            await store.transition(db, GUILD_ID, order.id, actor_id=200,
                                   owner_id=200, action="complete")
        processing = await store.transition(db, GUILD_ID, order.id,
                                            actor_id=300, action="confirm_payment", staff=True)
        assert processing.status == "processing"
        with pytest.raises(PermissionError):
            await store.transition(db, GUILD_ID, order.id, actor_id=200,
                                   owner_id=200, action="cancel")
        complete = await store.transition(db, GUILD_ID, order.id, actor_id=300,
                                          action="complete", staff=True)
        assert complete.status == "completed"
        assert len(await store.events(db, GUILD_ID, order.id)) == 4
        assert (await store.product_by_id(db, GUILD_ID, product.id)).stock is None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_concurrent_final_item_single_checkout(store_url):
    db = Database(store_url)
    try:
        item = await store.save_product(db, GUILD_ID, sku="last_item",
            title="Последний товар", price_minor=8500, stock=1)
        outcome = await asyncio.gather(
            store.make_order(db, GUILD_ID, 310, item.id, 9001),
            store.make_order(db, GUILD_ID, 311, item.id, 9002),
            return_exceptions=True)
        successful = [result for result in outcome if isinstance(result, tuple)]
        failed = [result for result in outcome if isinstance(result, ValueError)]
        assert len(successful) == 1 and len(failed) == 1, outcome
        assert (await store.product_by_id(db, GUILD_ID, item.id)).stock == 0
        assert len(await store.orders(db, GUILD_ID)) == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_open_order_limit_and_price_rejection_without_reservation(store_url):
    db = Database(store_url)
    try:
        item = await store.save_product(db, GUILD_ID, sku="service",
            title="Проверяемая услуга", price_minor=100, stock=5)
        for index in range(3):
            await store.make_order(db, GUILD_ID, 88, item.id, 1000 + index)
        with pytest.raises(ValueError, match="трёх незавершённых"):
            await store.make_order(db, GUILD_ID, 88, item.id, 1004)
        assert (await store.product_by_id(db, GUILD_ID, item.id)).stock == 2
        first = (await store.orders(db, GUILD_ID, user_id=88))[0]
        await store.transition(db, GUILD_ID, first.id, actor_id=88, owner_id=88, action="cancel")
        await store.make_order(db, GUILD_ID, 88, item.id, 1005)
        assert (await store.product_by_id(db, GUILD_ID, item.id)).stock == 2
    finally:
        await db.close()
