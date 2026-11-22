"""Owner-only store REST API: authentication, CSRF, historical prices and states."""
from __future__ import annotations

from aiohttp import CookieJar
from aiohttp.test_utils import TestClient, TestServer
import pytest

from app.config import GUILD_ID
from app.core.database import Database, migrate
from app.services import store
from app.web.auth import hash_password
from app.web.server import create_app
from tests.test_v20_staff import LocalBot, PASSWORD


@pytest.mark.asyncio
async def test_owner_store_http_api_is_private_and_no_payment_collection(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'storeapi.db'}"
    migrate(url)
    db = Database(url)
    app = create_app(LocalBot(db), hash_password(PASSWORD))
    try:
        async with TestClient(TestServer(app), cookie_jar=CookieJar(unsafe=True)) as client:
            assert (await client.get("/api/v1/store/products")).status == 401
            assert (await client.get("/api/v1/store/orders")).status == 401
            assert (await client.get("/api/v1/store/analytics")).status == 401
            origin = str(client.make_url("/")).rstrip("/")
            login = await client.post("/api/login", json={"password": PASSWORD},
                                      headers={"Origin": origin})
            assert login.status == 200
            csrf = (await login.json())["csrf"]
            correct = {"Origin": origin, "X-CSRF-Token": csrf}
            payload = {"sku": "nitro_1m", "title": "Nitro Gift 1 месяц",
                "description": "Передача подарка вручную", "category": "nitro_gift",
                "price_minor": 45000, "currency": "RUB", "stock": 2,
                "active": True, "sort_order": 0}
            assert (await client.post("/api/v1/store/products", json=payload,
                                      headers={"Origin": origin})).status == 403
            invalid = dict(payload)
            invalid["price_minor"] = "450.00"
            result = await client.post("/api/v1/store/products", json=invalid, headers=correct)
            assert result.status == 422
            result = await client.post("/api/v1/store/products", json=payload, headers=correct)
            assert result.status == 200, await result.text()
            item = (await result.json())["item"]
            assert item["price_minor"] == 45000 and item["sku"] == "nitro_1m"
            listed = await (await client.get("/api/v1/store/products")).json()
            assert len(listed["items"]) == 1
            assert (await client.get("/api/v1/store/settings")).status == 200
            # A web user must not configure a publicly visible, fake, or unknown Discord channel.
            bad_settings = await client.post("/api/v1/store/settings", headers=correct,
                json={"staff_channel_id": "1234",
                      "payment_instructions": "Оплата производится по инструкции продавца."})
            assert bad_settings.status == 422
            order, _ = await store.make_order(db, GUILD_ID, 700, item["id"], 998)
            listing = await (await client.get("/api/v1/store/orders")).json()
            assert len(listing["items"]) == 1
            assert listing["items"][0]["user_id"] == "700"
            assert "interaction_id" not in listing["items"][0]
            assert (await client.post("/api/v1/store/orders/1/action", json={
                "action": "complete"}, headers=correct)).status == 409
            assert (await client.post("/api/v1/store/orders/1/action", json={
                "action": "confirm_payment"}, headers=correct)).status == 200
            assert (await client.post("/api/v1/store/orders/1/action", json={
                "action": "complete"}, headers=correct)).status == 200
            history = await (await client.get(f"/api/v1/store/orders/{order.id}")).json()
            assert [event["to"] for event in history["history"]] == [
                "awaiting_payment", "processing", "completed"]
            analytics = await (await client.get("/api/v1/store/analytics")).json()
            assert analytics["counts"]["completed"] == 1
            assert analytics["completed_minor_by_currency"] == {"RUB": 45000}
            assert (await client.get("/api/staff/inbox")).status == 200
            assert (await client.get("/api/portal/applications")).status == 200
    finally:
        await db.close()


def test_store_discord_controls_support_persistent_panel_and_guild_guards():
    import discord

    from app.modules.store import Store
    from app.modules.store_views import StoreBuyerOrderView, StorePanelView, StoreStaffOrderView

    class Dummy:
        pass

    dummy = Dummy()
    public = StorePanelView(dummy)
    assert public.timeout is None
    assert {element.custom_id for element in public.children} == {
        "newsqad:store:catalog", "newsqad:store:mine"}
    controls = StoreStaffOrderView(dummy, 144)
    assert controls.timeout is None
    assert len({element.custom_id for element in controls.children}) == 3
    assert all(str(144) in element.custom_id for element in controls.children)
    assert StoreBuyerOrderView(dummy, 55, 23).timeout == 900
    assert isinstance(Store.store, discord.app_commands.Group)
