"""Транзакционная модель тикетов: не хранит токены и текст переписки."""
from __future__ import annotations

import time

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError

from app.models import SupportSurvey, Ticket, TicketCategory, TicketEvent, TicketPanel, TicketParticipant, WebNotice

DEFAULT_CATEGORIES = (
    ("support", "Поддержка", "Вопросы, ошибки и проблемы", 0),
    ("complaint", "Жалоба", "Жалобы на участников и нарушения", 1),
    ("other", "Другое", "Предложения и другие обращения", 2),
)
ACTIVE = ("creating", "open", "closing")


def safe_text(value: str, maximum: int) -> str:
    value = value.strip()
    if not value or len(value) > maximum:
        raise ValueError(f"Укажите от 1 до {maximum} символов.")
    if any(ord(c) < 32 and c not in "\t\n" for c in value):
        raise ValueError("Некорректные управляющие символы.")
    return value


def event(session, ticket_id: int, actor: int, action: str, **details) -> None:
    """В новой политике приватности события тикетов не сохраняются."""
    return None


async def erase_ticket(db, guild_id: int, ticket_id: int, *, closing_only: bool = True) -> bool:
    """Без транскриптов, журналов и записи обращения после закрытия.

    Канал Discord удаляет вызывающий код ДО удаления записи в БД.
    Все связанные строки удаляются в одной транзакции без AuditEntry.
    """
    async with db.session() as session:
        condition = (Ticket.id == ticket_id, Ticket.guild_id == guild_id)
        if closing_only:
            condition += (Ticket.status == "closing",)
        row = await session.scalar(select(Ticket).where(*condition))
        if row is None:
            return False
        # Сохраняем только приглашение оценить обслуживание и измерение времени
        # ответа. Ни тема, ни описание, ни чат, ни ticket_id не попадают в опрос.
        if closing_only and row.assignee_id and row.user_id != row.assignee_id:
            now = int(time.time())
            session.add(SupportSurvey(
                guild_id=guild_id, requester_id=row.user_id, staff_id=row.assignee_id,
                response_seconds=max(0, row.first_response_at - row.opened_at)
                    if row.first_response_at is not None else None,
                created_at=now, expires_at=now + 7 * 86400,
            ))
        await session.execute(delete(TicketEvent).where(TicketEvent.ticket_id == ticket_id))
        await session.execute(delete(TicketParticipant).where(TicketParticipant.ticket_id == ticket_id))
        # Уведомления о назначении удалённого приватного тикета также удаляем.
        await session.execute(delete(WebNotice).where(
            WebNotice.guild_id == guild_id, WebNotice.kind == "ticket_assigned",
            WebNotice.object_id == ticket_id))
        await session.execute(delete(Ticket).where(*condition))
        return True


async def seed_categories(db, guild_id: int) -> None:
    async with db.session() as session:
        for key, title, description, order in DEFAULT_CATEGORIES:
            stmt = sqlite_insert(TicketCategory).values(guild_id=guild_id, key=key, title=title,
                description=description, enabled=True, candidate_allowed=False, sort_order=order)
            await session.execute(stmt.on_conflict_do_nothing(index_elements=[TicketCategory.guild_id, TicketCategory.key]))


async def categories(db, guild_id: int, *, enabled_only: bool = True) -> list[TicketCategory]:
    async with db.sessions() as session:
        stmt = select(TicketCategory).where(TicketCategory.guild_id == guild_id)
        if enabled_only:
            stmt = stmt.where(TicketCategory.enabled.is_(True))
        return list((await session.scalars(stmt.order_by(TicketCategory.sort_order, TicketCategory.id))).all())


async def category(db, guild_id: int, category_id: int) -> TicketCategory | None:
    async with db.sessions() as session:
        return await session.scalar(select(TicketCategory).where(TicketCategory.guild_id == guild_id,
                                                                  TicketCategory.id == category_id))


async def add_category(db, guild_id: int, key: str, title: str, description: str, *, support_role_id: int | None,
                       candidate_allowed: bool) -> TicketCategory:
    key = safe_text(key.lower(), 40)
    if not key.replace("-", "").replace("_", "").isalnum() or not key.isascii():
        raise ValueError("Ключ категории: только латинские буквы, цифры, - и _.")
    async with db.session() as session:
        entry = TicketCategory(guild_id=guild_id, key=key, title=safe_text(title, 90),
            description=safe_text(description, 300), support_role_id=support_role_id,
            candidate_allowed=candidate_allowed, enabled=True, sort_order=100)
        session.add(entry)
        try:
            await session.flush()
        except IntegrityError as exc:
            raise ValueError("Категория с таким ключом уже существует.") from exc
        return entry


async def update_category(db, guild_id: int, category_id: int, *, title: str | None = None,
                          description: str | None = None, enabled: bool | None = None,
                          candidate_allowed: bool | None = None,
                          support_role_id: int | None = None, role_supplied: bool = False) -> bool:
    values = {}
    if title is not None:
        values["title"] = safe_text(title, 90)
    if description is not None:
        values["description"] = safe_text(description, 300)
    if enabled is not None:
        values["enabled"] = enabled
    if candidate_allowed is not None:
        values["candidate_allowed"] = candidate_allowed
    if role_supplied:
        values["support_role_id"] = support_role_id
    if not values:
        return False
    async with db.session() as session:
        result = await session.execute(update(TicketCategory).where(TicketCategory.id == category_id,
            TicketCategory.guild_id == guild_id).values(**values))
        return result.rowcount == 1


async def reserve(db, guild_id: int, user_id: int, category_id: int, subject: str, description: str,
                  *, support_role_id: int, max_open: int = 3) -> Ticket:
    subject, description = safe_text(subject, 100), safe_text(description, 1500)
    if not 1 <= max_open <= 10:
        raise ValueError("Неверный лимит открытых обращений.")
    now = int(time.time())
    async with db.session() as session:
        approved_category = await session.scalar(select(TicketCategory).where(
            TicketCategory.guild_id == guild_id, TicketCategory.id == category_id,
            TicketCategory.enabled.is_(True)))
        if not approved_category:
            raise ValueError("Категория обращения недоступна.")
        active_count = await session.scalar(select(func.count(Ticket.id)).where(
            Ticket.guild_id == guild_id, Ticket.user_id == user_id, Ticket.status.in_(ACTIVE)))
        if active_count >= max_open:
            raise ValueError("Достигнут лимит одновременно открытых обращений.")
        entry = Ticket(guild_id=guild_id, user_id=user_id, category_id=category_id,
            subject=subject, description=description, staff_role_id=support_role_id, opened_at=now, updated_at=now,
            status="creating", channel_deleted=False)
        session.add(entry)
        try:
            await session.flush()
        except IntegrityError as exc:
            raise ValueError("В этой категории у вас уже есть открытое обращение.") from exc
        event(session, entry.id, user_id, "created", category=approved_category.key)
        return entry


async def get(db, guild_id: int, ticket_id: int) -> Ticket | None:
    async with db.sessions() as session:
        return await session.scalar(select(Ticket).where(Ticket.id == ticket_id, Ticket.guild_id == guild_id))


async def by_channel(db, guild_id: int, channel_id: int) -> Ticket | None:
    async with db.sessions() as session:
        return await session.scalar(select(Ticket).where(Ticket.guild_id == guild_id, Ticket.channel_id == channel_id))


async def created(db, guild_id: int, ticket_id: int, channel_id: int) -> bool:
    now = int(time.time())
    async with db.session() as session:
        result = await session.execute(update(Ticket).where(Ticket.guild_id == guild_id,
            Ticket.id == ticket_id, Ticket.status == "creating").values(
            channel_id=channel_id, status="open", updated_at=now))
        if result.rowcount:
            event(session, ticket_id, 0, "channel_created", channel_id=channel_id)
        return result.rowcount == 1


async def fail_create(db, guild_id: int, ticket_id: int, reason: str) -> None:
    async with db.session() as session:
        result = await session.execute(update(Ticket).where(Ticket.guild_id == guild_id,
            Ticket.id == ticket_id, Ticket.status == "creating").values(status="failed", last_error=reason[:500],
                                                                      updated_at=int(time.time())))
        if result.rowcount:
            event(session, ticket_id, 0, "creation_failed", error=reason[:200])


async def claim(db, guild_id: int, ticket_id: int, actor_id: int, *, transfer: bool = False) -> bool:
    now = int(time.time())
    async with db.session() as session:
        stmt = update(Ticket).where(Ticket.id == ticket_id, Ticket.guild_id == guild_id,
                                    Ticket.status == "open")
        if not transfer:
            stmt = stmt.where(Ticket.assignee_id.is_(None))
        result = await session.execute(stmt.values(assignee_id=actor_id, updated_at=now))
        if result.rowcount:
            event(session, ticket_id, actor_id, "transferred" if transfer else "claimed", assignee_id=actor_id)
        return result.rowcount == 1


async def start_close(db, guild_id: int, ticket_id: int, actor_id: int, reason: str) -> bool:
    now = int(time.time())
    async with db.session() as session:
        result = await session.execute(update(Ticket).where(Ticket.id == ticket_id, Ticket.guild_id == guild_id,
            Ticket.status == "open").values(status="closing", closed_by=actor_id,
            close_reason=safe_text(reason, 500), updated_at=now))
        if result.rowcount:
            event(session, ticket_id, actor_id, "closing", reason=reason[:500])
        return result.rowcount == 1


async def finish_close(db, guild_id: int, ticket_id: int) -> bool:
    now = int(time.time())
    async with db.session() as session:
        result = await session.execute(update(Ticket).where(Ticket.id == ticket_id, Ticket.guild_id == guild_id,
            Ticket.status == "closing").values(status="closed", closed_at=now, updated_at=now))
        if result.rowcount:
            event(session, ticket_id, 0, "closed")
        return result.rowcount == 1


async def undo_close(db, guild_id: int, ticket_id: int, error: str) -> None:
    async with db.session() as session:
        await session.execute(update(Ticket).where(Ticket.id == ticket_id, Ticket.guild_id == guild_id,
            Ticket.status == "closing").values(status="open", last_error=error[:500],
                                               closed_at=None, closed_by=None, updated_at=int(time.time())))


async def reopen(db, guild_id: int, ticket_id: int, actor_id: int) -> bool:
    async with db.session() as session:
        try:
            result = await session.execute(update(Ticket).where(Ticket.id == ticket_id,
                Ticket.guild_id == guild_id, Ticket.status == "closed", Ticket.channel_deleted.is_(False),
                Ticket.channel_id.is_not(None)).values(status="open", closed_at=None, closed_by=None,
                    close_reason=None, updated_at=int(time.time())))
            if result.rowcount:
                event(session, ticket_id, actor_id, "reopened")
            return result.rowcount == 1
        except IntegrityError as exc:
            raise ValueError("У пользователя уже есть активное обращение в этой категории.") from exc


async def mark_deleted(db, guild_id: int, ticket_id: int, actor_id: int) -> None:
    async with db.session() as session:
        result = await session.execute(update(Ticket).where(Ticket.id == ticket_id, Ticket.guild_id == guild_id,
            Ticket.status == "closed", Ticket.channel_deleted.is_(False)).values(
                channel_deleted=True, updated_at=int(time.time())))
        if result.rowcount:
            event(session, ticket_id, actor_id, "archived")


async def participant(db, guild_id: int, ticket_id: int, user_id: int, actor_id: int, *, remove: bool) -> bool:
    async with db.session() as session:
        ticket = await session.scalar(select(Ticket).where(Ticket.id == ticket_id,
            Ticket.guild_id == guild_id, Ticket.status == "open"))
        if ticket is None:
            return False
        if user_id == ticket.user_id:
            raise ValueError("Автора обращения нельзя удалить из участников.")
        existing = await session.scalar(select(TicketParticipant).where(TicketParticipant.ticket_id == ticket_id,
                                                                        TicketParticipant.user_id == user_id))
        if remove:
            if existing is None:
                return False
            await session.delete(existing)
        else:
            if existing is not None:
                return False
            session.add(TicketParticipant(ticket_id=ticket_id, user_id=user_id, added_by=actor_id,
                                          added_at=int(time.time())))
        event(session, ticket_id, actor_id, "participant_removed" if remove else "participant_added", user_id=user_id)
        return True


async def participants(db, ticket_id: int) -> list[int]:
    async with db.sessions() as session:
        return list((await session.scalars(select(TicketParticipant.user_id).where(
            TicketParticipant.ticket_id == ticket_id))).all())


async def history(db, ticket_id: int, limit: int = 25) -> list[TicketEvent]:
    async with db.sessions() as session:
        return list((await session.scalars(select(TicketEvent).where(TicketEvent.ticket_id == ticket_id)
                            .order_by(TicketEvent.id.desc()).limit(limit))).all())


async def list_tickets(db, guild_id: int, *, statuses: tuple[str, ...] = ("open", "creating", "closing"),
                       user_id: int | None = None, limit: int = 25) -> list[Ticket]:
    async with db.sessions() as session:
        stmt = select(Ticket).where(Ticket.guild_id == guild_id, Ticket.status.in_(statuses))
        if user_id is not None:
            stmt = stmt.where(Ticket.user_id == user_id)
        return list((await session.scalars(stmt.order_by(Ticket.id.desc()).limit(limit))).all())


async def save_panel(db, guild_id: int, channel_id: int, message_id: int, actor_id: int) -> None:
    async with db.session() as session:
        session.add(TicketPanel(guild_id=guild_id, channel_id=channel_id, message_id=message_id,
                                enabled=True, created_by=actor_id, created_at=int(time.time())))


async def panel_active(db, guild_id: int, message_id: int) -> bool:
    async with db.sessions() as session:
        record = await session.scalar(select(TicketPanel.id).where(TicketPanel.guild_id == guild_id,
            TicketPanel.message_id == message_id, TicketPanel.enabled.is_(True)))
        return record is not None


async def save_control_message(db, guild_id: int, ticket_id: int, message_id: int) -> None:
    async with db.session() as session:
        await session.execute(update(Ticket).where(Ticket.id == ticket_id, Ticket.guild_id == guild_id,
            Ticket.status == "creating").values(control_message_id=message_id))


async def reconcile_missing_channel(db, guild_id: int, ticket_id: int) -> None:
    async with db.session() as session:
        row = await session.scalar(select(Ticket).where(Ticket.id == ticket_id, Ticket.guild_id == guild_id))
        if row is not None and row.status in ACTIVE:
            row.status = "failed"
            row.last_error = "Приватный канал удалён вне бота; требуется проверка."
            row.channel_deleted = True
            row.updated_at = int(time.time())
            event(session, ticket_id, 0, "channel_missing")


async def undo_reopen(db, guild_id: int, ticket_id: int, error: str) -> None:
    async with db.session() as session:
        await session.execute(update(Ticket).where(Ticket.id == ticket_id, Ticket.guild_id == guild_id,
            Ticket.status == "open").values(status="closed", last_error=error[:500], updated_at=int(time.time())))


async def touch(db, guild_id: int, ticket_id: int, actor_id: int, *, staff_reply: bool) -> int | None:
    now = int(time.time())
    async with db.session() as session:
        record = await session.scalar(select(Ticket).where(Ticket.id == ticket_id,
            Ticket.guild_id == guild_id, Ticket.status == "open"))
        if record is None:
            return None
        record.updated_at = now
        if staff_reply and record.first_response_at is None:
            record.first_response_at = now
            event(session, ticket_id, actor_id, "first_response")
        return now


async def stale_creating(db, guild_id: int, older_than: int) -> list[int]:
    async with db.sessions() as session:
        return list((await session.scalars(select(Ticket.id).where(Ticket.guild_id == guild_id,
            Ticket.status == "creating", Ticket.opened_at < older_than))).all())
