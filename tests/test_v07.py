"""v0.7.0: транзакции валюты, лимиты, магазин, титулы и события XP."""
from __future__ import annotations

import sqlite3

import pytest

from app.config import GUILD_ID, VERSION
from app.core.database import migrate
from app.services import economy as service


def test_validation_and_version():
    assert VERSION == "1.8.1"
    assert service.local_day(1_700_000_000, "Europe/Tallinn").isoformat() == "2023-11-15"
    assert service.code_field(" Role_Bronze ") == "role_bronze"
    with pytest.raises(ValueError):
        service.code_field("in valid")
    with pytest.raises(ValueError):
        service.check_amount(0)
    with pytest.raises(ValueError):
        service.check_amount(-1)
    with pytest.raises(ValueError):
        service.text_field("x" * 81, 80)


def test_economy_migration(tmp_path):
    file = tmp_path / "economy.db"
    url = f"sqlite+aiosqlite:///{file}"
    migrate(url)
    migrate(url)
    with sqlite3.connect(file) as sql:
        sql.execute("PRAGMA foreign_keys=ON")
        assert sql.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0015_support_live_ops"
        tables = {x[0] for x in sql.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"economy_wallets", "economy_ledger", "economy_shop_items", "economy_inventory"} <= tables
        sql.execute("INSERT INTO economy_wallets(guild_id,user_id,created_at,updated_at) VALUES (?,?,?,?)",
                    (GUILD_ID, 2, 1, 1))
        with pytest.raises(sqlite3.IntegrityError):
            sql.execute("INSERT INTO economy_wallets(guild_id,user_id,created_at,updated_at) VALUES (?,?,?,?)",
                        (GUILD_ID, 2, 1, 1))
        with pytest.raises(sqlite3.IntegrityError):
            sql.execute("UPDATE economy_wallets SET balance=-2 WHERE guild_id=?", (GUILD_ID,))
        sql.execute("INSERT INTO economy_ledger(guild_id,user_id,amount,kind,reference,created_at) "
                    "VALUES (?,?,?,?,?,?)", (GUILD_ID, 2, 20, "test", "one", 1))
        with pytest.raises(sqlite3.IntegrityError):
            sql.execute("INSERT INTO economy_ledger(guild_id,user_id,amount,kind,reference,created_at) "
                        "VALUES (?,?,?,?,?,?)", (GUILD_ID, 2, 20, "test", "one", 1))


@pytest.mark.asyncio
async def test_wallet_daily_and_transfer(tmp_path):
    pytest.importorskip("aiosqlite")
    from app.core.database import Database

    url = f"sqlite+aiosqlite:///{tmp_path / 'wallet.db'}"
    migrate(url)
    db = Database(url)
    try:
        first = await service.claim_daily(db, guild_id=GUILD_ID, user_id=1, timezone="UTC",
                                          now=1_700_000_000)
        assert first.amount == 100 and first.streak == 1 and first.balance == 100
        with pytest.raises(ValueError):
            await service.claim_daily(db, guild_id=GUILD_ID, user_id=1, timezone="UTC", now=1_700_000_100)
        second = await service.claim_daily(db, guild_id=GUILD_ID, user_id=1, timezone="UTC",
                                           now=1_700_000_000 + 86400)
        assert second.amount == 110 and second.streak == 2 and second.balance == 210
        await service.credit_once(db, guild_id=GUILD_ID, user_id=1, amount=50,
                                  kind="level", reference="test:1")
        repeated = await service.credit_once(db, guild_id=GUILD_ID, user_id=1, amount=50,
                                             kind="level", reference="test:1")
        assert repeated.repeated and repeated.amount == 0
        assert (await service.wallet(db, GUILD_ID, 1)).balance == 260
        moved = await service.transfer(db, guild_id=GUILD_ID, sender=1, recipient=2,
                                       amount=120, reference="move:1")
        assert moved.balance == 140
        assert (await service.wallet(db, GUILD_ID, 2)).balance == 120
        again = await service.transfer(db, guild_id=GUILD_ID, sender=1, recipient=2,
                                       amount=120, reference="move:1")
        assert again.repeated and (await service.wallet(db, GUILD_ID, 2)).balance == 120
        with pytest.raises(ValueError):
            await service.transfer(db, guild_id=GUILD_ID, sender=1, recipient=2,
                                   amount=150, reference="move:2")
        assert (await service.wallet(db, GUILD_ID, 2)).balance == 120
        with pytest.raises(ValueError):
            await service.adjust(db, guild_id=GUILD_ID, user_id=1, delta=-200,
                                 actor_id=5, reference="admin:1")
        assert (await service.wallet(db, GUILD_ID, 1)).balance == 140
        assert len(await service.ledger(db, GUILD_ID, 1)) >= 4
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_shop_stock_inventory_and_title(tmp_path):
    pytest.importorskip("aiosqlite")
    from app.core.database import Database

    url = f"sqlite+aiosqlite:///{tmp_path / 'shop.db'}"
    migrate(url)
    db = Database(url)
    try:
        product = await service.add_item(db, guild_id=GUILD_ID, code="veteran", title="Ветеран",
                                         kind="title", price=60, stock=1)
        await service.credit_once(db, guild_id=GUILD_ID, user_id=7, amount=200,
                                  kind="test", reference="setup:7")
        owned = await service.purchase(db, guild_id=GUILD_ID, user_id=7, item_id=product.id)
        assert owned.delivery == "not_required"
        assert (await service.wallet(db, GUILD_ID, 7)).balance == 140
        with pytest.raises(ValueError):
            await service.purchase(db, guild_id=GUILD_ID, user_id=7, item_id=product.id)
        with pytest.raises(ValueError):
            await service.purchase(db, guild_id=GUILD_ID, user_id=8, item_id=product.id)
        assert (await service.wallet(db, GUILD_ID, 7)).balance == 140
        await service.equip_title(db, GUILD_ID, 7, product.id)
        assert await service.equipped_title(db, GUILD_ID, 7) == "Ветеран"
        with pytest.raises(ValueError):
            await service.equip_title(db, GUILD_ID, 8, product.id)
        await service.equip_title(db, GUILD_ID, 7, None)
        assert await service.equipped_title(db, GUILD_ID, 7) is None
        badge = await service.add_item(db, guild_id=GUILD_ID, code="role", title="Серый",
                                       kind="role", price=10, role_id=123)
        assert badge.kind == "role"
        await service.purchase(db, guild_id=GUILD_ID, user_id=7, item_id=badge.id)
        assert len(await service.pending_roles(db, GUILD_ID, 7)) == 1
        await service.mark_role_delivered(db, GUILD_ID, 7, badge.id)
        assert not await service.pending_roles(db, GUILD_ID, 7)
        assert await service.set_item_active(db, GUILD_ID, badge.id, False)
        assert len(await service.inventory(db, GUILD_ID, 7)) == 2
    finally:
        await db.close()


def test_cog_and_settings_registered():
    discord = pytest.importorskip("discord")
    from app.modules.economy import Economy
    from app.panels.botsettings import HomeView
    from app.panels.settings_overview import PAGE_OPTIONS

    assert Economy.economy.name == "economy"
    assert Economy.shop.name == "shop"
    assert Economy.inventory.name == "inventory"
    assert "economy" in {row[0] for row in PAGE_OPTIONS}

    class Stub:
        config = type("Cfg", (), {"owner_ids": frozenset()})()

    view = HomeView(Stub(), 1)
    assert "Экономика" in {option.label for item in view.children if isinstance(item, discord.ui.Select)
                             for option in item.options}
