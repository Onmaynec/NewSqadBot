"""Security invariant: every mutable web API requires auth + CSRF.

Runs against the actual aiohttp route table, including routes registered by
portal/owner/community/live-ops modules, not a manually maintained allowlist.
"""
from __future__ import annotations

import re
from types import SimpleNamespace

import pytest
from aiohttp import CookieJar
from aiohttp.test_utils import TestClient, TestServer

from app.web.auth import hash_password
from app.web.server import create_app


def api_mutations(app):
    routes = []
    for route in app.router.routes():
        if route.method not in ("POST", "PUT", "PATCH", "DELETE"):
            continue
        path = route.resource.canonical
        if not path.startswith("/api/") or path == "/api/login":
            continue
        routes.append((route.method, re.sub(r"\{[^}]+\}", "1", path)))
    return sorted(set(routes))


@pytest.mark.asyncio
async def test_each_mutation_route_denies_anonymous_and_csrf_less_requests():
    bot = SimpleNamespace(config=SimpleNamespace(guild_id=1439347293076586638),
                          get_guild=lambda _gid: None, is_ready=lambda: False)
    application = create_app(bot, hash_password("valid local admin password 123"))
    routes = api_mutations(application)
    assert len(routes) >= 12, "Route list must cover all portal mutations"

    async with TestClient(TestServer(application), cookie_jar=CookieJar(unsafe=True)) as client:
        origin = str(client.make_url("/")).rstrip("/")
        for method, path in routes:
            result = await client.request(method, path, json={}, headers={"Origin": origin})
            assert result.status in (401, 403), (method, path, result.status)
        login = await client.post("/api/login", json={"password": "valid local admin password 123"},
                                  headers={"Origin": origin})
        assert login.status == 200
        record = await login.json()
        assert record["csrf"]
        for method, path in routes:
            result = await client.request(method, path, json={}, headers={"Origin": origin})
            assert result.status == 403, (method, path, result.status)
            # Invalid CSRF must never execute writes even when cookie is valid.
            result = await client.request(method, path, json={},
                                          headers={"Origin": origin, "X-CSRF-Token": "invalid"})
            assert result.status == 403, (method, path, result.status)


@pytest.mark.asyncio
async def test_cross_origin_requests_fail_before_any_mutation():
    bot = SimpleNamespace(config=SimpleNamespace(guild_id=1439347293076586638),
                          get_guild=lambda _gid: None, is_ready=lambda: False)
    async with TestClient(TestServer(create_app(bot, hash_password("valid local admin password 123"))),
                          cookie_jar=CookieJar(unsafe=True)) as client:
        res = await client.post("/api/login", json={"password": "valid local admin password 123"},
                                headers={"Origin": "https://attacker.invalid"})
        assert res.status == 403
