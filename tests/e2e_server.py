"""Local-only real aiohttp server for browser E2E tests (no Discord connection).

Starts the real app, auth, SQLite and static assets with a fake Discord guild.
Never started by production code. Accessible at 127.0.0.1 only.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

from aiohttp import web

from app.config import GUILD_ID
from app.core.database import Database, migrate
from app.web.auth import hash_password
from app.web.server import create_app

PASSWORD = "Local test password 2026!"


class Guild:
    id = GUILD_ID
    name = "NewSquad E2E"
    owner_id = 1
    member_count = 15
    icon = None
    banner = None
    roles = ()
    channels = ()
    members = ()
    me = None

    def get_role(self, _id):
        return None

    def get_channel(self, _id):
        return None


class FakeBot:
    def __init__(self, database: Database):
        self.database = database
        self.config = SimpleNamespace(guild_id=GUILD_ID)
        self.module_states = {"voice": "active", "membership": "active"}
        self.guild = Guild()
        self.scheduler = None
        self.events = None
        self.operations = None
        self.backups = None

    def get_guild(self, gid):
        return self.guild if gid == GUILD_ID else None

    def is_ready(self):
        return True


def main():
    directory = tempfile.TemporaryDirectory(prefix="newsqad-playwright-")
    db_file = Path(directory.name) / "test.db"
    url = f"sqlite+aiosqlite:///{db_file}"
    migrate(url)
    database = Database(url)
    app = create_app(FakeBot(database), hash_password(PASSWORD))

    async def cleanup(_app):
        await database.close()
        directory.cleanup()

    app.on_cleanup.append(cleanup)
    web.run_app(app, host="127.0.0.1", port=9876, access_log=None, print=None)


if __name__ == "__main__":
    main()
