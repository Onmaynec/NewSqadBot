"""Versioned local staff API contracts and CSRF enforcement."""
import json
from pathlib import Path

import pytest
from aiohttp import CookieJar
from aiohttp.test_utils import TestClient, TestServer

from app.core.database import Database, migrate
from app.web.auth import hash_password
from app.web.server import create_app
from tests.test_v20_staff import LocalBot

PASS = "A local operator password for tests"


@pytest.mark.asyncio
async def test_local_v1_contract_and_csrf(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'api.db'}"
    migrate(url)
    db = Database(url)
    try:
        app = create_app(LocalBot(db), hash_password(PASS))
        async with TestClient(TestServer(app), cookie_jar=CookieJar(unsafe=True)) as client:
            assert (await client.get("/api/v1/contracts")).status == 401
            origin = str(client.make_url("/")).rstrip("/")
            login = await client.post("/api/login", json={"password": PASS},
                                      headers={"Origin": origin})
            assert login.status == 200
            csrf = (await login.json())["csrf"]
            schema = await (await client.get("/api/v1/contracts")).json()
            reference = json.loads((Path(__file__).resolve().parents[1] /
                                    "docs/api_contract_v1.json").read_text(encoding="utf-8"))
            assert schema == reference
            denied = await client.post("/api/v1/membership/forms",
                json={"key": "team", "title": "Командная",
                      "question": "Вы в составе команды?"},
                headers={"Origin": origin})
            assert denied.status == 403
            authorized = {"Origin": origin, "X-CSRF-Token": csrf}
            saved = await client.post("/api/v1/membership/forms",
                json={"key": "team", "title": "Командная",
                      "question": "Вы в составе команды?"},
                headers=authorized)
            assert saved.status == 200, await saved.text()
            assert (await client.get("/api/v1/membership/forms")).status == 200
            assert (await client.get("/api/v1/voice/usage")).status == 200
            assert (await client.get("/api/portal/applications")).status == 200
            invalid = await client.post("/api/v1/membership/onboarding",
                json={"config": {"rules": "x", "tasks": ["hi"], "channel_ids": []}},
                headers=authorized)
            assert invalid.status == 422
    finally:
        await db.close()


def test_contract_version_lists_actual_routes():
    from app.web import v21

    schema = json.loads(v21.SCHEMA_FILE.read_text(encoding="utf-8"))
    assert schema["version"] == v21.CONTRACT_VERSION
    assert len(schema["resources"]) == 11
    assert all(path.startswith(("GET /api/v1/", "POST /api/v1/")) for path in schema["resources"])
