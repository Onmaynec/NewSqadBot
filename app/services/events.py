"""Транзакционные операции Events. Все дедлайны в Unix UTC."""
from __future__ import annotations

import json
import secrets
import time
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.models import (
    CommunityEvent,
    EventReminder,
    EventSignup,
    ExperienceAward,
    Giveaway,
    GiveawayDraw,
    GiveawayEntry,
)
from app.services import economy, progression


def clean_text(raw: str, limit: int) -> str:
    value = " ".join(raw.strip().split())
    if not value or len(value) > limit or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError(f"Текст должен содержать от 1 до {limit} символов.")
    return value


def local_to_utc(value: str, timezone_name: str) -> int:
    """Не принимать несуществующее или двусмысленное время при переходе DST."""
    try:
        zone = ZoneInfo(timezone_name)
        naive = datetime.strptime(value.strip(), "%Y-%m-%d %H:%M")
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ValueError("Укажите дату как ГГГГ-ММ-ДД ЧЧ:ММ и корректный часовой пояс.") from exc
    left = naive.replace(tzinfo=zone, fold=0)
    right = naive.replace(tzinfo=zone, fold=1)
    if left.utcoffset() != right.utcoffset():
        raise ValueError("Это местное время неоднозначно или отсутствует из-за перевода часов.")
    timestamp = int(left.timestamp())
    if datetime.fromtimestamp(timestamp, zone).replace(tzinfo=None) != naive:
        raise ValueError("Местное время не существует.")
    return timestamp


def reminder_offsets(raw: str) -> tuple[int, ...]:
    """Список минут до начала: 60,15. Максимум четыре уведомления."""
    if not raw.strip():
        return ()
    try:
        values = [int(part.strip()) for part in raw.split(",")]
    except ValueError as exc:
        raise ValueError("Интервалы напоминаний: 60,15 (минуты).") from exc
    if len(values) > 4 or len(set(values)) != len(values) or any(v < 1 or v > 10080 for v in values):
        raise ValueError("Не более четырёх уникальных интервалов от 1 до 10080 минут.")
    return tuple(sorted(values, reverse=True))


async def create_event(db, *, guild_id: int, creator_id: int, host_id: int, channel_id: int,
                       title: str, description: str, timezone_name: str, starts_at: int,
                       duration_minutes: int = 120, registration_closes_minutes: int = 0,
                       capacity: int = 0, waitlist_enabled: bool = True, reminders: str = "60,15",
                       reward_xp: int = 0, reward_coins: int = 0, now: int | None = None) -> CommunityEvent:
    now = int(time.time()) if now is None else now
    title, description = clean_text(title, 100), clean_text(description, 800)
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("Неизвестный часовой пояс.") from exc
    if not now + 60 <= starts_at <= now + 90 * 86400:
        raise ValueError("Начало должно быть через 1 минуту — 90 дней.")
    if not 15 <= duration_minutes <= 1440:
        raise ValueError("Длительность мероприятия: 15–1440 минут.")
    if not 0 <= registration_closes_minutes <= 10080 or starts_at - registration_closes_minutes * 60 <= now:
        raise ValueError("Закрытие регистрации должно быть в будущем и до начала.")
    if not 0 <= capacity <= 1000:
        raise ValueError("Лимит участников: 0–1000, 0 — без лимита.")
    if not 0 <= reward_xp <= 1000 or not 0 <= reward_coins <= 1000:
        raise ValueError("Награды: 0–1000 XP или NS Coins.")
    offsets = reminder_offsets(reminders)
    async with db.session() as session:
        row = CommunityEvent(guild_id=guild_id, creator_id=creator_id, host_id=host_id,
            channel_id=channel_id, title=title, description=description, timezone=timezone_name,
            starts_at=starts_at, ends_at=starts_at + duration_minutes * 60,
            registration_ends_at=starts_at - registration_closes_minutes * 60,
            capacity=capacity, waitlist_enabled=bool(waitlist_enabled), confirmed_count=0,
            waitlist_count=0, reminders=",".join(map(str, offsets)),
            reward_xp=reward_xp, reward_coins=reward_coins, status="scheduled",
            created_at=now, updated_at=now)
        session.add(row)
        await session.flush()
        event_id = row.id
        for minutes in offsets:
            session.add(EventReminder(event_id=event_id, minutes_before=minutes))
    return await get_event(db, guild_id, event_id)


async def get_event(db, guild_id: int, event_id: int) -> CommunityEvent | None:
    async with db.sessions() as session:
        return await session.scalar(select(CommunityEvent).where(
            CommunityEvent.id == event_id, CommunityEvent.guild_id == guild_id))


async def list_events(db, guild_id: int, *, limit: int = 25) -> list[CommunityEvent]:
    async with db.sessions() as session:
        return list((await session.scalars(select(CommunityEvent).where(
            CommunityEvent.guild_id == guild_id).order_by(CommunityEvent.id.desc()).limit(limit))).all())


async def active_events(db, guild_id: int) -> list[CommunityEvent]:
    async with db.sessions() as session:
        return list((await session.scalars(select(CommunityEvent).where(
            CommunityEvent.guild_id == guild_id,
            CommunityEvent.status.in_(("scheduled", "live"))
        ).order_by(CommunityEvent.starts_at))).all())


async def event_signups(db, event_id: int, *, status: str | None = None) -> list[EventSignup]:
    async with db.sessions() as session:
        stmt = select(EventSignup).where(EventSignup.event_id == event_id)
        if status:
            stmt = stmt.where(EventSignup.status == status)
        rows = await session.scalars(stmt.order_by(EventSignup.registered_at, EventSignup.id))
        return list(rows.all())


async def register(db, guild_id: int, event_id: int, user_id: int, *, now: int | None = None) -> str:
    now = int(time.time()) if now is None else now
    async with db.session() as session:
        # Первая запись берёт SQLite write lock и сериализует конкурирующие регистрации.
        locked = await session.execute(update(CommunityEvent).where(
            CommunityEvent.id == event_id, CommunityEvent.guild_id == guild_id,
            CommunityEvent.status == "scheduled", CommunityEvent.registration_ends_at > now
        ).values(updated_at=now))
        if locked.rowcount != 1:
            raise ValueError("Регистрация закрыта или мероприятие не найдено.")
        event = await session.get(CommunityEvent, event_id)
        old = await session.scalar(select(EventSignup).where(
            EventSignup.event_id == event_id, EventSignup.user_id == user_id))
        if old is not None and old.status != "cancelled":
            raise ValueError("Вы уже зарегистрированы.")
        if event.capacity and event.confirmed_count >= event.capacity:
            if not event.waitlist_enabled or event.waitlist_count >= 1000:
                raise ValueError("Все места заняты, очередь ожидания отключена или заполнена.")
            status = "waitlist"
            event.waitlist_count += 1
        else:
            status = "registered"
            event.confirmed_count += 1
        if old is None:
            session.add(EventSignup(event_id=event_id, user_id=user_id,
                                    status=status, registered_at=now))
        else:
            old.status, old.registered_at = status, now
        return status


async def withdraw(db, guild_id: int, event_id: int, user_id: int,
                   *, now: int | None = None) -> int | None:
    now = int(time.time()) if now is None else now
    async with db.session() as session:
        locked = await session.execute(update(CommunityEvent).where(
            CommunityEvent.id == event_id, CommunityEvent.guild_id == guild_id,
            CommunityEvent.status == "scheduled", CommunityEvent.starts_at > now
        ).values(updated_at=now))
        if locked.rowcount != 1:
            raise ValueError("Регистрация уже завершена.")
        event = await session.get(CommunityEvent, event_id)
        signup = await session.scalar(select(EventSignup).where(
            EventSignup.event_id == event_id, EventSignup.user_id == user_id))
        if signup is None or signup.status not in ("registered", "waitlist"):
            raise ValueError("Вы не записаны на мероприятие.")
        old_status = signup.status
        signup.status = "cancelled"
        promoted = None
        if old_status == "waitlist":
            event.waitlist_count -= 1
        else:
            event.confirmed_count -= 1
            if event.waitlist_count:
                next_user = await session.scalar(select(EventSignup).where(
                    EventSignup.event_id == event_id, EventSignup.status == "waitlist"
                ).order_by(EventSignup.registered_at, EventSignup.id).limit(1))
                if next_user is not None:
                    next_user.status = "registered"
                    event.waitlist_count -= 1
                    event.confirmed_count += 1
                    promoted = next_user.user_id
    return promoted


async def cancel_event(db, guild_id: int, event_id: int, *, now: int | None = None) -> bool:
    now = int(time.time()) if now is None else now
    async with db.session() as session:
        result = await session.execute(update(CommunityEvent).where(
            CommunityEvent.id == event_id, CommunityEvent.guild_id == guild_id,
            CommunityEvent.status.in_(("scheduled", "live"))
        ).values(status="cancelled", updated_at=now))
        return result.rowcount == 1


async def mark_live(db, guild_id: int, event_id: int, *, now: int | None = None) -> bool:
    now = int(time.time()) if now is None else now
    async with db.session() as session:
        result = await session.execute(update(CommunityEvent).where(
            CommunityEvent.id == event_id, CommunityEvent.guild_id == guild_id,
            CommunityEvent.status == "scheduled", CommunityEvent.starts_at <= now
        ).values(status="live", updated_at=now))
        return result.rowcount == 1


async def mark_finished(db, guild_id: int, event_id: int, *, now: int | None = None) -> bool:
    now = int(time.time()) if now is None else now
    async with db.session() as session:
        result = await session.execute(update(CommunityEvent).where(
            CommunityEvent.id == event_id, CommunityEvent.guild_id == guild_id,
            CommunityEvent.status.in_(("scheduled", "live")), CommunityEvent.ends_at <= now
        ).values(status="finished", updated_at=now))
        return result.rowcount == 1


async def reminder_state(db, guild_id: int, event_id: int, minutes: int):
    async with db.sessions() as session:
        return (await session.execute(select(EventReminder.sent_at, CommunityEvent.starts_at,
            CommunityEvent.status, CommunityEvent.channel_id, CommunityEvent.title).join(
            CommunityEvent, CommunityEvent.id == EventReminder.event_id).where(
            CommunityEvent.id == event_id, CommunityEvent.guild_id == guild_id,
            EventReminder.minutes_before == minutes))).one_or_none()


async def mark_reminder_sent(db, event_id: int, minutes: int, *, now: int | None = None) -> bool:
    now = int(time.time()) if now is None else now
    async with db.session() as session:
        result = await session.execute(update(EventReminder).where(
            EventReminder.event_id == event_id, EventReminder.minutes_before == minutes,
            EventReminder.sent_at.is_(None)).values(sent_at=now))
        return result.rowcount == 1


async def set_event_message(db, guild_id: int, event_id: int, message_id: int) -> None:
    async with db.session() as session:
        await session.execute(update(CommunityEvent).where(
            CommunityEvent.id == event_id, CommunityEvent.guild_id == guild_id,
            CommunityEvent.message_id.is_(None)).values(message_id=message_id))


async def confirm_attendance(db, *, guild_id: int, event_id: int, user_id: int,
                             actor_id: int, timezone_name: str,
                             now: int | None = None) -> tuple[int, int]:
    """Одно подтверждение и обе награды — единая транзакция."""
    now = int(time.time()) if now is None else now
    async with db.session() as session:
        event = await session.scalar(select(CommunityEvent).where(
            CommunityEvent.id == event_id, CommunityEvent.guild_id == guild_id))
        if event is None or event.status not in ("live", "finished") or now < event.starts_at:
            raise ValueError("Подтвердить участие можно только после начала мероприятия.")
        result = await session.execute(update(EventSignup).where(
            EventSignup.event_id == event_id, EventSignup.user_id == user_id,
            EventSignup.status == "registered", EventSignup.attended_at.is_(None)
        ).values(attended_at=now, confirmed_by=actor_id))
        if result.rowcount != 1:
            raise ValueError("Участник не зарегистрирован или уже подтверждён.")
        awarded_xp = 0
        if event.reward_xp:
            row = await progression._progress(session, guild_id, user_id, now)
            progression._roll_day(row, progression.day_key(now, timezone_name))
            outcome = await progression._credit(session, row, amount=event.reward_xp,
                source="event", reference=str(event_id), now=now, cap=row.daily_xp + event.reward_xp)
            awarded_xp = outcome.amount
            session.add(ExperienceAward(guild_id=guild_id, user_id=user_id, source="event",
                reference=str(event_id), amount=awarded_xp, created_at=now))
        if event.reward_coins:
            await economy._apply(session, guild_id=guild_id, user_id=user_id, amount=event.reward_coins,
                kind="event", reference=f"event:{event_id}:{user_id}", now=now, actor_id=actor_id)
        return awarded_xp, event.reward_coins


async def create_giveaway(db, *, guild_id: int, creator_id: int, channel_id: int, prize: str,
                          conditions: str, ends_at: int, winner_count: int = 1,
                          required_role_id: int | None = None, min_level: int = 0,
                          now: int | None = None) -> Giveaway:
    now = int(time.time()) if now is None else now
    prize = clean_text(prize, 200)
    conditions = clean_text(conditions, 500)
    if not now + 60 <= ends_at <= now + 30 * 86400:
        raise ValueError("Срок розыгрыша: от минуты до 30 дней.")
    if not 1 <= winner_count <= 20 or not 0 <= min_level <= 1000:
        raise ValueError("Число победителей: 1–20; минимальный уровень: 0–1000.")
    async with db.session() as session:
        row = Giveaway(guild_id=guild_id, creator_id=creator_id, channel_id=channel_id,
            prize=prize, conditions=conditions, ends_at=ends_at, winner_count=winner_count,
            required_role_id=required_role_id, min_level=min_level, status="open",
            entry_count=0, winners_json="[]", draw_round=0, created_at=now)
        session.add(row)
        await session.flush()
        giveaway_id = row.id
    return await get_giveaway(db, guild_id, giveaway_id)


async def get_giveaway(db, guild_id: int, giveaway_id: int) -> Giveaway | None:
    async with db.sessions() as session:
        return await session.scalar(select(Giveaway).where(
            Giveaway.id == giveaway_id, Giveaway.guild_id == guild_id))


async def list_giveaways(db, guild_id: int, limit: int = 25) -> list[Giveaway]:
    async with db.sessions() as session:
        return list((await session.scalars(select(Giveaway).where(
            Giveaway.guild_id == guild_id).order_by(Giveaway.id.desc()).limit(limit))).all())


async def open_giveaways(db, guild_id: int) -> list[Giveaway]:
    async with db.sessions() as session:
        return list((await session.scalars(select(Giveaway).where(
            Giveaway.guild_id == guild_id, Giveaway.status == "open"
        ).order_by(Giveaway.ends_at))).all())


async def giveaway_entrants(db, giveaway_id: int) -> list[int]:
    async with db.sessions() as session:
        rows = await session.scalars(select(GiveawayEntry.user_id).where(
            GiveawayEntry.giveaway_id == giveaway_id).order_by(GiveawayEntry.id))
        return list(rows.all())


async def enter_giveaway(db, guild_id: int, giveaway_id: int, user_id: int,
                         *, now: int | None = None) -> None:
    now = int(time.time()) if now is None else now
    async with db.session() as session:
        result = await session.execute(update(Giveaway).where(
            Giveaway.id == giveaway_id, Giveaway.guild_id == guild_id,
            Giveaway.status == "open", Giveaway.ends_at > now,
            Giveaway.entry_count < 5000
        ).values(entry_count=Giveaway.entry_count + 1))
        if result.rowcount != 1:
            raise ValueError("Розыгрыш завершён или достигнут лимит участников.")
        statement = sqlite_insert(GiveawayEntry).values(
            giveaway_id=giveaway_id, user_id=user_id, joined_at=now
        ).on_conflict_do_nothing(index_elements=[GiveawayEntry.giveaway_id, GiveawayEntry.user_id])
        inserted = await session.execute(statement)
        if inserted.rowcount != 1:
            raise ValueError("Вы уже участвуете в розыгрыше.")
        # При ValueError выше транзакция откатывает и увеличение счётчика.


async def set_giveaway_message(db, guild_id: int, giveaway_id: int, message_id: int) -> None:
    async with db.session() as session:
        await session.execute(update(Giveaway).where(
            Giveaway.id == giveaway_id, Giveaway.guild_id == guild_id,
            Giveaway.message_id.is_(None)).values(message_id=message_id))


async def draw_giveaway(db, *, guild_id: int, giveaway_id: int, eligible_ids: list[int],
                        actor_id: int, reason: str, force: bool = False,
                        now: int | None = None, rng=None) -> list[int]:
    """Фиксируем окончание и неизменяемый снимок допущенных одной транзакцией."""
    now = int(time.time()) if now is None else now
    async with db.session() as session:
        stmt = update(Giveaway).where(Giveaway.id == giveaway_id, Giveaway.guild_id == guild_id,
                                      Giveaway.status == "open")
        if not force:
            stmt = stmt.where(Giveaway.ends_at <= now)
        changed = await session.execute(stmt.values(status="ended", finished_at=now))
        if changed.rowcount != 1:
            raise ValueError("Розыгрыш ещё не завершён или итоги уже подведены.")
        row = await session.get(Giveaway, giveaway_id)
        entrants = set((await session.scalars(select(GiveawayEntry.user_id).where(
            GiveawayEntry.giveaway_id == giveaway_id))).all())
        pool = sorted(set(eligible_ids) & entrants)
        winners = (rng or secrets.SystemRandom()).sample(pool, min(row.winner_count, len(pool)))
        row.draw_round = 1
        row.winners_json = json.dumps(winners)
        session.add(GiveawayDraw(giveaway_id=giveaway_id, round=1,
            actor_id=actor_id, reason=clean_text(reason, 400),
            eligible_json=json.dumps(pool), winners_json=json.dumps(winners),
            algorithm="secrets.SystemRandom.sample", created_at=now))
    return winners


async def reroll_giveaway(db, *, guild_id: int, giveaway_id: int,
                          actor_id: int, reason: str, now: int | None = None, rng=None) -> list[int]:
    reason = clean_text(reason, 400)
    now = int(time.time()) if now is None else now
    async with db.session() as session:
        # UPDATE первым сериализует два одновременных reroll.
        result = await session.execute(update(Giveaway).where(
            Giveaway.id == giveaway_id, Giveaway.guild_id == guild_id,
            Giveaway.status == "ended", Giveaway.draw_round >= 1
        ).values(draw_round=Giveaway.draw_round + 1))
        if result.rowcount != 1:
            raise ValueError("Розыгрыш не найден или ещё не завершён.")
        row = await session.get(Giveaway, giveaway_id)
        first = await session.scalar(select(GiveawayDraw).where(
            GiveawayDraw.giveaway_id == giveaway_id, GiveawayDraw.round == 1))
        eligible = json.loads(first.eligible_json)
        previous_draws = (await session.scalars(select(GiveawayDraw).where(
            GiveawayDraw.giveaway_id == giveaway_id))).all()
        selected_before = {uid for draw in previous_draws for uid in json.loads(draw.winners_json)}
        choices = [uid for uid in eligible if uid not in selected_before]
        if not choices:
            raise ValueError("Нет новых кандидатов для повторного выбора.")
        winners = (rng or secrets.SystemRandom()).sample(choices, min(row.winner_count, len(choices)))
        row.winners_json = json.dumps(winners)
        session.add(GiveawayDraw(giveaway_id=giveaway_id, round=row.draw_round,
            actor_id=actor_id, reason=reason, eligible_json=first.eligible_json,
            winners_json=json.dumps(winners), algorithm="secrets.SystemRandom.sample", created_at=now))
    return winners


async def giveaway_draws(db, giveaway_id: int) -> list[GiveawayDraw]:
    async with db.sessions() as session:
        rows = await session.scalars(select(GiveawayDraw).where(
            GiveawayDraw.giveaway_id == giveaway_id).order_by(GiveawayDraw.round))
        return list(rows.all())
