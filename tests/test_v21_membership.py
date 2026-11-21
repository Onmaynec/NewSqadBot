"""v2.1 member journey: SQLite schema, staged review and privacy."""
import sqlite3

import pytest

from app.config import GUILD_ID
from app.core.database import Database, migrate
from app.services import applications, membership_workflow


def test_schema_v21_migration(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'journey.db'}"
    migrate(url)
    migrate(url)
    with sqlite3.connect(tmp_path / "journey.db") as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").scalar if False else True
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0017_member_support_voice"
        names = {x[0] for x in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"membership_form_types", "membership_checklist_items",
                "voice_owner_preferences", "voice_usage_days", "tickets"} <= names


@pytest.mark.asyncio
async def test_form_and_review_privacy(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'data.db'}"
    migrate(url)
    db = Database(url)
    try:
        await membership_workflow.save_form(db, GUILD_ID, key="team",
            title="Командная заявка", question="Есть ли опыт участия в команде?")
        app = await applications.submit(db, guild_id=GUILD_ID, user_id=999,
            username="newbie", name="Alex", age=20, gender="male", inviter="friend",
            form_key="team", extra_answer="Опыт два сезона")
        assert app.form_key == "team"
        checks = await membership_workflow.checklist(db, GUILD_ID, app.id)
        assert len(checks) == 3
        assert await membership_workflow.set_check(db, GUILD_ID, app.id,
            key="rules", completed=True, actor_id=61)
        assert (await membership_workflow.checklist(db, GUILD_ID, app.id))[0]["completed"]
        assert await applications.transition(db, app.id, expected=("pending",),
            target="needs_info", actor_id=61, reason="Уточнить состав")
        assert await applications.submit_answer(db, GUILD_ID, app.id, 999, "Ответ при проверке")
        timeline = await applications.timeline(db, GUILD_ID, app.id)
        assert all("Ответ при проверке" not in row.reason for row in timeline)
        assert await applications.transition(db, app.id, expected=("in_review",),
            target="approved", actor_id=61, reason="Допущен")
        approved = await applications.get_application(db, app.id)
        assert approved.extra_answer is None and approved.followup_answer is None
    finally:
        await db.close()
