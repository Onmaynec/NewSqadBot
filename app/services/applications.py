"""SQL операции с заявками: блокировка повторов и история решений."""
from __future__ import annotations

import json
import time

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError

from app.models import Application, ApplicationEvent, OutboxEvent

OPEN = ("pending", "in_review", "needs_info", "approving", "rejecting", "action_failed")
REVIEWABLE = ("pending", "in_review", "needs_info")


async def submit(db, *, guild_id: int, user_id: int, username: str, name: str,
                 gender: str, age: int, inviter: str) -> Application:
    if gender not in ("male", "female") or not 1 <= age <= 120:
        raise ValueError("Некорректные данные анкеты.")
    if not inviter or len(inviter) > 120 or len(name) > 24:
        raise ValueError("Некорректная длина поля анкеты.")
    try:
        async with db.session() as session:
            application = Application(guild_id=guild_id, user_id=user_id,
                username=username[:64], name=name, gender=gender, age=age, inviter=inviter,
                status="pending", created_at=int(time.time()))
            session.add(application)
            await session.flush()
            session.add(ApplicationEvent(application_id=application.id, actor_id=user_id,
                old_status="new", new_status="pending", reason="Заявка отправлена", created_at=int(time.time())))
            result_id = application.id
    except IntegrityError as exc:
        raise ValueError("У вас уже есть активная заявка.") from exc
    return await get_application(db, result_id)


async def get_application(db, app_id: int) -> Application | None:
    async with db.sessions() as session:
        return await session.get(Application, app_id)


async def get_active(db, guild_id: int, user_id: int) -> Application | None:
    async with db.sessions() as session:
        return await session.scalar(select(Application).where(
            Application.guild_id == guild_id, Application.user_id == user_id,
            Application.status.in_(OPEN)).order_by(Application.id.desc()).limit(1))


async def past_applications(db, guild_id: int, user_id: int, limit: int = 10) -> list[Application]:
    async with db.sessions() as session:
        rows = await session.scalars(select(Application).where(
            Application.guild_id == guild_id, Application.user_id == user_id
        ).order_by(Application.id.desc()).limit(limit))
        return list(rows.all())


async def awaiting_publication(db, guild_id: int) -> list[Application]:
    async with db.sessions() as session:
        rows = await session.scalars(select(Application).where(
            Application.guild_id == guild_id, Application.status.in_(OPEN),
            Application.review_message_id.is_(None)).order_by(Application.id).limit(100))
        return list(rows.all())


async def unfinished(db, guild_id: int, limit: int = 100) -> list[Application]:
    """Заявки с принятым решением, но незавершёнными Discord-действиями."""
    async with db.sessions() as session:
        rows = await session.scalars(select(Application).where(
            Application.guild_id == guild_id,
            Application.status.in_(("approving", "rejecting", "action_failed"))
        ).order_by(Application.id).limit(limit))
        return list(rows.all())


async def active_reviews(db, guild_id: int) -> list[Application]:
    async with db.sessions() as session:
        rows = await session.scalars(select(Application).where(
            Application.guild_id == guild_id, Application.status.in_(OPEN),
            Application.review_message_id.is_not(None)).order_by(Application.id.desc()).limit(500))
        return list(rows.all())


async def set_review_message(db, app_id: int, channel_id: int, message_id: int) -> None:
    async with db.session() as session:
        await session.execute(update(Application).where(
            Application.id == app_id, Application.review_message_id.is_(None)
        ).values(review_channel_id=channel_id, review_message_id=message_id))


async def transition(db, app_id: int, *, expected: tuple[str, ...], target: str,
                     actor_id: int, reason: str, decision: str | None = None) -> bool:
    now = int(time.time())
    async with db.session() as session:
        application = await session.get(Application, app_id)
        if application is None or application.status not in expected:
            return False
        old = application.status
        stmt = update(Application).where(Application.id == app_id, Application.status == old).values(
            status=target, reviewer_id=actor_id,
            **({"decision": decision, "decision_reason": reason, "decided_at": now} if decision else {}))
        result = await session.execute(stmt)
        if result.rowcount != 1:
            return False
        session.add(ApplicationEvent(application_id=app_id, actor_id=actor_id,
            old_status=old, new_status=target, reason=reason, created_at=now))
        if target == "approved":
            # Статус APPROVED и событие Welcome/авторолей сохраняются одной транзакцией.
            payload = json.dumps({"guild_id": application.guild_id,
                "user_id": application.user_id, "application_id": app_id}, ensure_ascii=False)
            stmt = sqlite_insert(OutboxEvent).values(
                id=f"membership-approved-{app_id}", kind="membership.approved",
                payload_json=payload, created_at=now)
            await session.execute(stmt.on_conflict_do_nothing(index_elements=[OutboxEvent.id]))
    return True


async def step(db, app_id: int, key: str, status: str, error: str | None = None) -> None:
    allowed = {"role_status", "female_role_status", "candidate_role_status", "nickname_status", "kick_status", "dm_status"}
    if key not in allowed or status not in {"pending", "done", "failed", "n_a"}:
        raise ValueError("Неизвестный шаг обработки")
    async with db.session() as session:
        await session.execute(update(Application).where(Application.id == app_id).values(
            **{key: status}, last_error=error[:500] if error else None))


async def fail(db, app_id: int, actor_id: int, reason: str) -> None:
    row = await get_application(db, app_id)
    if row:
        await transition(db, app_id, expected=(row.status,), target="action_failed",
                         actor_id=actor_id, reason=reason)
