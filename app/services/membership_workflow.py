"""Shared membership workflow rules used by Discord and the local admin console.

Only a short optional answer is retained while a candidate is being reviewed;
checklists store checkboxes and action attribution, not new identity documents.
"""
from __future__ import annotations

import re
import time

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.models import Application, ApplicationChecklistItem, ApplicationEvent, ApplicationFormType

FORM_KEY = re.compile(r"[a-z][a-z0-9_]{1,31}\Z")
DEFAULT_CHECKS = (
    ("rules", "Ознакомлен с правилами"),
    ("profile", "Данные анкеты проверены"),
    ("access", "Проверены права и доступ"),
)
DEFAULT_FORM = {"key": "standard", "title": "Обычная заявка", "question": "", "enabled": True}


async def form_types(db, guild_id: int, *, enabled_only: bool = False) -> list[dict]:
    async with db.sessions() as session:
        stmt = select(ApplicationFormType).where(ApplicationFormType.guild_id == guild_id)
        if enabled_only:
            stmt = stmt.where(ApplicationFormType.enabled.is_(True))
        rows = (await session.scalars(stmt.order_by(ApplicationFormType.id).limit(25))).all()
        return [DEFAULT_FORM.copy(), *[
            {"key": row.key, "title": row.title, "question": row.question, "enabled": row.enabled}
            for row in rows
        ]]


async def selected_form(db, guild_id: int, key: str):
    if key == "standard":
        return DEFAULT_FORM.copy()
    forms = await form_types(db, guild_id, enabled_only=True)
    return next((form for form in forms if form["key"] == key and form["enabled"]), None)


async def save_form(db, guild_id: int, *, key: str, title: str, question: str,
                    enabled: bool = True) -> None:
    if not FORM_KEY.fullmatch(key) or key == "standard":
        raise ValueError("Ключ формы: 2–32 строчных латинских символа, цифры и _; standard зарезервирован.")
    title, question = title.strip(), question.strip()
    if not 3 <= len(title) <= 80 or not 3 <= len(question) <= 100:
        raise ValueError("Название формы: 3–80 символов, вопрос: 3–100 символов.")
    if type(enabled) is not bool:
        raise ValueError("enabled должен быть boolean")
    now = int(time.time())
    async with db.session() as session:
        count = await session.scalar(select(func.count()).select_from(ApplicationFormType).where(
            ApplicationFormType.guild_id == guild_id))
        existing = await session.scalar(select(ApplicationFormType.id).where(
            ApplicationFormType.guild_id == guild_id, ApplicationFormType.key == key))
        if existing is None and count >= 8:
            raise ValueError("Можно создать не более 8 типов анкет.")
        stmt = sqlite_insert(ApplicationFormType).values(
            guild_id=guild_id, key=key, title=title, question=question,
            enabled=enabled, created_at=now, updated_at=now)
        await session.execute(stmt.on_conflict_do_update(
            index_elements=["guild_id", "key"],
            set_={"title": title, "question": question, "enabled": enabled, "updated_at": now}))


async def checklist(db, guild_id: int, application_id: int) -> list[dict]:
    async with db.sessions() as session:
        exists = await session.scalar(select(Application.id).where(
            Application.id == application_id, Application.guild_id == guild_id))
        if exists is None:
            return []
        rows = (await session.scalars(select(ApplicationChecklistItem).where(
            ApplicationChecklistItem.application_id == application_id
        ).order_by(ApplicationChecklistItem.id))).all()
        return [{"key": item.item_key, "title": item.title, "completed": item.completed,
                 "actor_id": str(item.actor_id) if item.actor_id else None,
                 "changed_at": item.changed_at} for item in rows]


async def set_check(db, guild_id: int, application_id: int, *, key: str,
                    completed: bool, actor_id: int, title: str = "") -> bool:
    if not FORM_KEY.fullmatch(key) or type(completed) is not bool or actor_id <= 0:
        raise ValueError("Некорректное значение чек-листа.")
    if title and not 3 <= len(title.strip()) <= 100:
        raise ValueError("Название проверки: 3–100 символов.")
    now = int(time.time())
    async with db.session() as session:
        app = await session.scalar(select(Application).where(
            Application.id == application_id, Application.guild_id == guild_id))
        if app is None or app.status not in ("pending", "in_review", "needs_info"):
            return False
        row = await session.scalar(select(ApplicationChecklistItem).where(
            ApplicationChecklistItem.application_id == application_id,
            ApplicationChecklistItem.item_key == key))
        if row is None:
            if not title:
                raise ValueError("Для новой проверки укажите название.")
            count = await session.scalar(select(func.count()).select_from(ApplicationChecklistItem).where(
                ApplicationChecklistItem.application_id == application_id))
            if count >= 12:
                raise ValueError("Не более 12 проверок на заявку.")
            row = ApplicationChecklistItem(application_id=application_id,
                item_key=key, title=title.strip(), completed=completed, actor_id=actor_id,
                changed_at=now)
            session.add(row)
        else:
            if row.completed == completed:
                return True
            row.completed, row.actor_id, row.changed_at = completed, actor_id, now
        # Do not place candidate answers or identifying info in the status journal.
        session.add(ApplicationEvent(application_id=application_id, actor_id=actor_id,
            old_status=app.status, new_status=app.status,
            reason="Чек-лист: " + key + (" выполнен" if completed else " снят"),
            created_at=now))
    return True


def review_due_at(application, *, waiting_hours: int) -> int:
    return (application.stage_changed_at or application.created_at) + waiting_hours * 3600
