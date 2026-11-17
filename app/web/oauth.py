"""Discord OAuth2 identify + live role-based authorization for NewSquad web.

Only the bot can read private ticket channels; user OAuth tokens are never stored.
"""
from __future__ import annotations

import hmac
import logging
import secrets
import time
from urllib.parse import urlencode

import aiohttp
import discord
from aiohttp import web

log = logging.getLogger(__name__)

OWNER_ROLE = 1439347293391159652
ADMIN_ROLE = 1440528252933374072
MODER_ROLE = 1439347293391159651
TICKET_ROLE = 1440528284504035358

ROLE_IDS = {"owner": OWNER_ROLE, "admin": ADMIN_ROLE, "moder": MODER_ROLE, "support": TICKET_ROLE}
RANK = {"support": 1, "moder": 2, "admin": 3, "owner": 4}
API = "https://discord.com/api/v10"


def site_role(member, guild) -> str | None:
    """Use live guild roles, not values supplied by a browser or OAuth token."""
    roles = {role.id for role in member.roles}
    if OWNER_ROLE in roles or member.id == guild.owner_id:
        return "owner"
    if ADMIN_ROLE in roles:
        return "admin"
    if MODER_ROLE in roles:
        return "moder"
    if TICKET_ROLE in roles:
        return "support"
    return None


async def guild_member(bot, user_id: int):
    guild = bot.get_guild(bot.config.guild_id)
    if guild is None or not bot.is_ready():
        raise web.HTTPServiceUnavailable(text="Discord временно недоступен.")
    try:
        member = await guild.fetch_member(user_id)
    except discord.NotFound as exc:
        raise web.HTTPForbidden(text="Вы не состоите на сервере NewSquad.") from exc
    except discord.HTTPException as exc:
        log.warning("Discord: не удалось перепроверить роль посетителя")
        raise web.HTTPServiceUnavailable(text="Не удалось проверить Discord-роли. Повторите позже.") from exc
    return guild, member


async def permission(bot, user_id: int, minimum: str) -> tuple[str, discord.Member]:
    guild, member = await guild_member(bot, user_id)
    role = site_role(member, guild)
    if role is None or RANK[role] < RANK[minimum]:
        raise web.HTTPForbidden(text="Доступ к этому разделу не разрешён.")
    return role, member


def authorize_url(client_id: str, redirect_uri: str, oauth_state: str) -> str:
    return "https://discord.com/oauth2/authorize?" + urlencode({
        "client_id": client_id, "redirect_uri": redirect_uri,
        "response_type": "code", "scope": "identify",
        "state": oauth_state, "prompt": "consent",
    })


async def exchange_code(client_id: str, secret: str, redirect_uri: str, code: str) -> int:
    """No session persistence of Discord bearer token, only verified user ID."""
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as http:
        async with http.post(f"{API}/oauth2/token", data={
            "client_id": client_id, "client_secret": secret,
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": redirect_uri,
        }) as response:
            if response.status != 200:
                raise web.HTTPBadGateway(text="Discord отклонил авторизацию.")
            token = (await response.json()).get("access_token", "")
        if not token:
            raise web.HTTPBadGateway(text="Discord не вернул ключ авторизации.")
        async with http.get(f"{API}/users/@me",
                            headers={"Authorization": f"Bearer {token}"}) as response:
            if response.status != 200:
                raise web.HTTPBadGateway(text="Discord не подтвердил пользователя.")
            user = await response.json()
    try:
        return int(user["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise web.HTTPBadGateway(text="Discord вернул некорректный идентификатор.") from exc


def consume_state(states: dict[str, float], supplied: str | None, cookie: str | None) -> bool:
    if not isinstance(supplied, str) or not isinstance(cookie, str):
        return False
    expires = states.pop(supplied, 0.0)
    return bool(expires > time.time() and hmac.compare_digest(supplied, cookie))


def new_state(states: dict[str, float]) -> str:
    now = time.time()
    for key, expires in list(states.items()):
        if expires < now:
            states.pop(key, None)
    if len(states) >= 128:
        oldest = min(states, key=states.get)
        states.pop(oldest, None)
    value = secrets.token_urlsafe(32)
    states[value] = now + 300
    return value
