"""Персональные уведомления веб-панели без хранения содержимого обращений.

Уведомления строятся по фактическому состоянию SQLite. Повторный опрос
идемпотентен, права проверяются на HTTP-уровне при каждом чтении и ack.
"""
from __future__ import annotations

import time

from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.models import (
    Application,
    CommunityEvent,
    InventoryItem,
    TemporaryRole,
    Ticket,
    WebNotice,
    WebNoticeRead,
)

RETENTION = 30 * 86400
MAX_ITEMS = 50
KINDS = {
    "application": ("Новая заявка", "Заявка ожидает рассмотрения", "Заявки"),
    "ticket_assigned": ("Назначен тикет", "На вас назначено обращение", "Тикеты"),
    "role_failure": ("Ошибка выдачи роли", "Проверьте неудачную операцию с ролью", "Аудит"),
    "event_upcoming": ("Скоро мероприятие", "Проверьте предстоящее мероприятие", "Мероприятия"),
}


async def sync_sources(db, guild_id: int, *, now: int | None = None) -> int:
    """Ограниченный скан; дедупликация SQL UNIQUE предотвращает повторные notices."""
    now = int(time.time()) if now is None else now
    start = now - RETENTION
    additions: list[dict] = []

    def enqueue(key: str, kind: str, target: int | None, object_id: int,
                at: int, role: str = "support") -> None:
        additions.append(dict(
            guild_id=guild_id, dedupe_key=key, kind=kind,
            recipient_id=target, object_id=object_id,
            created_at=at, minimum_role=role))

    async with db.sessions() as session:
        apps = (await session.scalars(select(Application).where(
            Application.guild_id == guild_id,
            Application.created_at >= now - 7 * 86400,
            Application.status.in_(("pending", "in_review", "needs_info", "action_failed",
                                    "approving", "rejecting"))).order_by(
            Application.created_at.desc()).limit(100))).all()
        for row in apps:
            enqueue(f"application:{row.id}", "application", None, row.id, row.created_at)

        assigned = (await session.scalars(select(Ticket).where(
            Ticket.guild_id == guild_id, Ticket.status == "open",
            Ticket.assignee_id.is_not(None)).order_by(Ticket.id.desc()).limit(100))).all()
        for row in assigned:
            enqueue(f"ticket_assigned:{row.id}:{row.assignee_id}", "ticket_assigned",
                    row.assignee_id, row.id, row.updated_at)

        failures = (await session.scalars(select(TemporaryRole).where(
            TemporaryRole.guild_id == guild_id, TemporaryRole.status == "failed",
            TemporaryRole.granted_at >= start).order_by(
            TemporaryRole.id.desc()).limit(50))).all()
        for row in failures:
            enqueue(f"temporary_role_error:{row.id}", "role_failure", None,
                    row.id, row.granted_at, "admin")
        failed_inventory = (await session.scalars(select(InventoryItem).where(
            InventoryItem.guild_id == guild_id, InventoryItem.delivery == "failed",
            InventoryItem.purchased_at >= start).order_by(
            InventoryItem.id.desc()).limit(50))).all()
        for row in failed_inventory:
            enqueue(f"purchase_role_error:{row.id}", "role_failure", None,
                    row.id, row.purchased_at, "admin")
        for row in apps:
            if row.status == "action_failed" and row.role_status == "failed":
                enqueue(f"membership_role_error:{row.id}", "role_failure", None,
                        row.id, row.created_at, "admin")

        upcoming = (await session.scalars(select(CommunityEvent).where(
            CommunityEvent.guild_id == guild_id, CommunityEvent.status == "scheduled",
            CommunityEvent.starts_at >= now, CommunityEvent.starts_at <= now + 3600)
            .order_by(CommunityEvent.starts_at).limit(50))).all()
        for row in upcoming:
            enqueue(f"upcoming_event:{row.id}", "event_upcoming", None,
                    row.id, now, "admin")

    async with db.session() as session:
        await session.execute(delete(WebNotice).where(
            WebNotice.guild_id == guild_id, WebNotice.created_at < start))
        for data in additions:
            stmt = sqlite_insert(WebNotice).values(**data)
            await session.execute(stmt.on_conflict_do_nothing(
                index_elements=[WebNotice.guild_id, WebNotice.dedupe_key]))
    return len(additions)


def permitted(role: str, notice: WebNotice, user_id: int) -> bool:
    from app.web.staff_roles import RANK

    return (RANK.get(role, 0) >= RANK.get(notice.minimum_role, 99)
            and (notice.recipient_id is None or notice.recipient_id == user_id))


async def inbox(db, guild_id: int, user_id: int, role: str):
    from app.web.staff_roles import RANK

    async with db.sessions() as session:
        # Filter recipient AND staff role in SQL; no cross-user leakage.
        query = select(WebNotice, WebNoticeRead.id).outerjoin(
            WebNoticeRead,
            (WebNoticeRead.notice_id == WebNotice.id) &
            (WebNoticeRead.user_id == user_id)).where(
            WebNotice.guild_id == guild_id,
            or_(WebNotice.recipient_id.is_(None), WebNotice.recipient_id == user_id),
        )
        allowed = [key for key, rank in RANK.items() if rank <= RANK.get(role, 0)]
        rows = (await session.execute(query.where(
            WebNotice.minimum_role.in_(allowed))
            .order_by(WebNotice.created_at.desc(), WebNotice.id.desc())
            .limit(MAX_ITEMS))).all()
        unread = await session.scalar(select(func.count(WebNotice.id)).outerjoin(
            WebNoticeRead, (WebNoticeRead.notice_id == WebNotice.id) &
            (WebNoticeRead.user_id == user_id)).where(
            WebNotice.guild_id == guild_id,
            WebNotice.minimum_role.in_(allowed),
            or_(WebNotice.recipient_id.is_(None), WebNotice.recipient_id == user_id),
            WebNoticeRead.id.is_(None)))
    return {
        "unread": int(unread or 0),
        "items": [
            {"id": note.id, "kind": note.kind, "title": KINDS[note.kind][0],
             "description": KINDS[note.kind][1],
             "section": KINDS[note.kind][2],
             "object_id": note.object_id, "created_at": note.created_at,
             "read": read_id is not None}
            for note, read_id in rows
        ],
    }


async def mark_read(db, guild_id: int, user_id: int, role: str,
                    *, notice_id: int | None = None, now: int | None = None) -> int:
    now = int(time.time()) if now is None else now
    from app.web.staff_roles import RANK

    allowed = [key for key, rank in RANK.items() if rank <= RANK.get(role, 0)]
    async with db.session() as session:
        query = select(WebNotice.id).where(
            WebNotice.guild_id == guild_id,
            WebNotice.minimum_role.in_(allowed),
            or_(WebNotice.recipient_id.is_(None), WebNotice.recipient_id == user_id))
        if notice_id is not None:
            query = query.where(WebNotice.id == notice_id)
        ids = (await session.scalars(query)).all()
        for key in ids:
            statement = sqlite_insert(WebNoticeRead).values(
                notice_id=key, user_id=user_id, read_at=now)
            await session.execute(statement.on_conflict_do_nothing(
                index_elements=[WebNoticeRead.notice_id, WebNoticeRead.user_id]))
    return len(ids)
