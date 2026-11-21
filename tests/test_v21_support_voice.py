"""v2.1 support SLAs and privacy-safe Voice usage regressions."""
import pytest
from sqlalchemy import func, select

from app.config import GUILD_ID
from app.core.database import Database, migrate
from app.models import VoiceUsageDay
from app.services import support_sla, tickets, voice, voice_preferences


@pytest.mark.asyncio
async def test_priorities_and_sla_config(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'support.db'}"
    migrate(url)
    db = Database(url)
    try:
        await tickets.seed_categories(db, GUILD_ID)
        category = (await tickets.categories(db, GUILD_ID))[0]
        ticket = await tickets.reserve(db, GUILD_ID, 555, category.id,
            "Срочная помощь", "Описание", support_role_id=998)
        await tickets.created(db, GUILD_ID, ticket.id, 991)
        assert await support_sla.change_priority(db, GUILD_ID, ticket.id, "urgent", actor_id=11)
        current = await tickets.get(db, GUILD_ID, ticket.id)
        rules = await support_sla.get_rules(db, GUILD_ID)
        indicators = support_sla.indicators(current, rules, now=current.opened_at + 1801)
        assert indicators["first_breached"] and indicators["priority"] == "urgent"
        with pytest.raises(ValueError):
            await support_sla.change_priority(db, GUILD_ID, ticket.id, "unknown", actor_id=11)
        custom = dict(rules)
        custom["urgent"] = {"first_minutes": 15, "idle_minutes": 90}
        assert (await support_sla.save_rules(
            db, GUILD_ID, custom, actor_id=11))["urgent"]["first_minutes"] == 15
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_voice_templates_lifecycle_and_aggregated_usage(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'voice.db'}"
    migrate(url)
    db = Database(url)
    try:
        await voice_preferences.save(db, GUILD_ID, 33, template="private",
            name="Комната друзей", limit=8, locked=True, hidden=True)
        prefs = await voice_preferences.preference(db, GUILD_ID, 33)
        assert prefs.template == "private" and prefs.hidden
        room = await voice.register(db, guild_id=GUILD_ID, channel_id=4999,
            owner_id=33, lobby_id=900, name="Комната друзей", user_limit=8)
        await voice.set_access(db, room.id, 41, "allow")
        await voice.set_access(db, room.id, 42, "deny")
        assert len(await voice.access_entries(db, room.id)) == 2
        await voice.transfer(db, GUILD_ID, 4999, 34)
        assert (await voice.by_owner(db, GUILD_ID, 34)).channel_id == 4999
        await voice.remove(db, GUILD_ID, 4999)
        first = await voice_preferences.usage(db, GUILD_ID)
        await voice.remove(db, GUILD_ID, 4999)
        assert await voice_preferences.usage(db, GUILD_ID) == first
        assert sum(day["rooms_created"] for day in first) == 1
        async with db.sessions() as session:
            assert await session.scalar(select(func.count()).select_from(VoiceUsageDay)) == 1
    finally:
        await db.close()
