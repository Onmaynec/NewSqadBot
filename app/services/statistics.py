"""Статистика NewSquad: дневные агрегаты без хранения содержимого сообщений."""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, time as clock_time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.models import (
    AchievementUnlock,
    Application,
    ApplicationEvent,
    CommunityEvent,
    EventSignup,
    MemberProfile,
    MemberProgress,
    PartyListing,
    PartyMember,
    QuestClaim,
    StatsDay,
    StatsPanel,
    StatsPresence,
    StatsReceipt,
    StatsServerDay,
    Ticket,
    Wallet,
)
from app.services.progression import day_key

PERIOD_DAYS = {"7d": 7, "30d": 30, "90d": 90}


@dataclass(frozen=True)
class Period:
    key: str
    first: str
    last: str
    start_timestamp: int
    end_timestamp: int
    timezone: str


@dataclass(frozen=True)
class ServerTotals:
    total: int
    approved: int
    candidates: int
    joins: int
    leaves: int
    approvals: int
    rejections: int
    messages: int
    voice_minutes: int
    active_users: int
    events: int
    parties: int
    tickets: int


@dataclass(frozen=True)
class UserTotals:
    level: int
    xp: int
    credited_messages: int
    credited_voice_minutes: int
    tracked_messages: int
    tracked_voice_minutes: int
    achievements: int
    quests: int
    event_registrations: int
    event_attendance: int
    parties: int
    wallet_balance: int | None
    public: bool


def period_bounds(key: str, *, timezone: str, now: int | None = None) -> Period:
    if key not in PERIOD_DAYS:
        raise ValueError("Период должен быть 7d, 30d или 90d.")
    now = int(time.time()) if now is None else now
    zone = ZoneInfo(timezone)
    last_day = datetime.fromtimestamp(now, zone).date()
    first_day = last_day - timedelta(days=PERIOD_DAYS[key] - 1)
    end_day = last_day + timedelta(days=1)
    return Period(
        key=key, first=first_day.isoformat(), last=last_day.isoformat(),
        start_timestamp=int(datetime.combine(first_day, clock_time.min, zone).timestamp()),
        end_timestamp=int(datetime.combine(end_day, clock_time.min, zone).timestamp()),
        timezone=timezone,
    )


async def record_activity(db, *, guild_id: int, user_id: int, kind: str,
                          reference: int, timestamp: int, timezone: str) -> bool:
    """Учитывает событие максимум один раз даже при конкурентных callbacks."""
    if kind not in ("message", "voice") or reference < 0:
        raise ValueError("Неизвестный тип или идентификатор активности.")
    day = day_key(timestamp, timezone)
    async with db.session() as session:
        stmt = sqlite_insert(StatsReceipt).values(
            guild_id=guild_id, user_id=user_id,
            kind=kind, reference=str(reference), created_at=timestamp,
        ).on_conflict_do_nothing(index_elements=[
            StatsReceipt.guild_id, StatsReceipt.user_id,
            StatsReceipt.kind, StatsReceipt.reference,
        ])
        outcome = await session.execute(stmt)
        if outcome.rowcount != 1:
            return False
        increment_messages = 1 if kind == "message" else 0
        increment_voice = 1 if kind == "voice" else 0
        counter = sqlite_insert(StatsDay).values(
            guild_id=guild_id, user_id=user_id, day=day,
            messages=increment_messages, voice_minutes=increment_voice,
        ).on_conflict_do_update(
            index_elements=[StatsDay.guild_id, StatsDay.user_id, StatsDay.day],
            set_={
                "messages": StatsDay.messages + increment_messages,
                "voice_minutes": StatsDay.voice_minutes + increment_voice,
            },
        )
        await session.execute(counter)
    return True


async def record_presence(db, *, guild_id: int, user_id: int,
                          joined: bool, timezone: str, now: int | None = None) -> bool:
    """Двойное событие join/leave не прибавляет счётчик повторно."""
    now = int(time.time()) if now is None else now
    stmt = sqlite_insert(StatsPresence).values(
        guild_id=guild_id, user_id=user_id, present=joined, changed_at=now,
    ).on_conflict_do_update(
        index_elements=[StatsPresence.guild_id, StatsPresence.user_id],
        set_={"present": joined, "changed_at": now},
        where=StatsPresence.present.is_(not joined),
    )
    async with db.session() as session:
        updated = await session.execute(stmt)
        if updated.rowcount != 1:
            return False
        daily = sqlite_insert(StatsServerDay).values(
            guild_id=guild_id, day=day_key(now, timezone),
            joins=int(joined), leaves=int(not joined),
        ).on_conflict_do_update(
            index_elements=[StatsServerDay.guild_id, StatsServerDay.day],
            set_={
                "joins": StatsServerDay.joins + int(joined),
                "leaves": StatsServerDay.leaves + int(not joined),
            },
        )
        await session.execute(daily)
    return True


async def bootstrap_presence(db, guild_id: int, member_ids: list[int],
                             *, now: int | None = None) -> None:
    """Участники до включения учёта — baseline, а не новые вступления."""
    now = int(time.time()) if now is None else now
    async with db.session() as session:
        for uid in member_ids:
            await session.execute(sqlite_insert(StatsPresence).values(
                guild_id=guild_id, user_id=uid, present=True, changed_at=now,
            ).on_conflict_do_update(
                index_elements=[StatsPresence.guild_id, StatsPresence.user_id],
                set_={"present": True, "changed_at": now},
                where=StatsPresence.present.is_(False),
            ))


async def prune_receipts(db, *, now: int | None = None, days: int = 35) -> int:
    """Удаляем только квитанции дедупликации, не дневные агрегаты."""
    now = int(time.time()) if now is None else now
    async with db.session() as session:
        result = await session.execute(delete(StatsReceipt).where(
            StatsReceipt.created_at < now - days * 86400,
        ))
        return result.rowcount


async def approved_ids(db, guild_id: int, members: list,
                       member_role_id: int | None) -> set[int]:
    """Одобренные живые участники по последней анкете и актуальной роли."""
    if not member_role_id:
        return set()
    member_by_id = {m.id: m for m in members if not m.bot}
    latest = select(
        Application.user_id, func.max(Application.id).label("latest_id"),
    ).where(Application.guild_id == guild_id).group_by(Application.user_id).subquery()
    async with db.sessions() as session:
        approvals = (await session.execute(select(
            Application.user_id, Application.decided_at,
        ).join(latest, Application.id == latest.c.latest_id).where(
            Application.guild_id == guild_id, Application.status == "approved",
        ))).all()
    found = set()
    for uid, approved_at in approvals:
        member = member_by_id.get(uid)
        if member is None or not any(r.id == member_role_id for r in member.roles):
            continue
        joined_at = getattr(member, "joined_at", None)
        if joined_at and approved_at and int(joined_at.timestamp()) > approved_at:
            continue
        found.add(uid)
    return found


async def server_totals(db, *, guild_id: int, period: Period,
                        members: list, member_count: int,
                        member_role_id: int | None, candidate_role_id: int | None,
                        tracking_started_at: int) -> ServerTotals:
    """Одобрение проверяется по последней заявке, а не только по Discord-роли."""
    member_by_id = {m.id: m for m in members if not m.bot}
    eligible_ids = await approved_ids(db, guild_id, members, member_role_id)
    candidates = sum(
        1 for m in member_by_id.values()
        if m.id not in eligible_ids and candidate_role_id
        and any(r.id == candidate_role_id for r in m.roles)
    )
    async with db.sessions() as session:
        messages, voice, users = (await session.execute(select(
            func.coalesce(func.sum(StatsDay.messages), 0),
            func.coalesce(func.sum(StatsDay.voice_minutes), 0),
            func.count(func.distinct(StatsDay.user_id)),
        ).where(
            StatsDay.guild_id == guild_id,
            StatsDay.day.between(period.first, period.last),
            or_(StatsDay.messages > 0, StatsDay.voice_minutes > 0),
        ))).one()
        joins, leaves = (await session.execute(select(
            func.coalesce(func.sum(StatsServerDay.joins), 0),
            func.coalesce(func.sum(StatsServerDay.leaves), 0),
        ).where(
            StatsServerDay.guild_id == guild_id,
            StatsServerDay.day.between(period.first, period.last),
        ))).one()
        status_rows = (await session.execute(select(
            ApplicationEvent.new_status, func.count(ApplicationEvent.id),
        ).join(
            Application, ApplicationEvent.application_id == Application.id,
        ).where(
            Application.guild_id == guild_id,
            ApplicationEvent.created_at >= max(period.start_timestamp, tracking_started_at),
            ApplicationEvent.created_at < period.end_timestamp,
            ApplicationEvent.new_status.in_(("approved", "rejected")),
        ).group_by(ApplicationEvent.new_status))).all()
        event_count = await session.scalar(select(func.count()).select_from(
            CommunityEvent).where(
            CommunityEvent.guild_id == guild_id,
            CommunityEvent.created_at >= max(period.start_timestamp, tracking_started_at),
            CommunityEvent.created_at < period.end_timestamp,
        ))
        party_count = await session.scalar(select(func.count()).select_from(
            PartyListing).where(
            PartyListing.guild_id == guild_id,
            PartyListing.created_at >= max(period.start_timestamp, tracking_started_at),
            PartyListing.created_at < period.end_timestamp,
        ))
        ticket_count = await session.scalar(select(func.count()).select_from(
            Ticket).where(
            Ticket.guild_id == guild_id,
            Ticket.created_at >= max(period.start_timestamp, tracking_started_at),
            Ticket.created_at < period.end_timestamp,
        ))
    membership_stats = dict(status_rows)
    return ServerTotals(
        total=member_count, approved=len(eligible_ids), candidates=candidates,
        joins=joins, leaves=leaves,
        approvals=membership_stats.get("approved", 0),
        rejections=membership_stats.get("rejected", 0),
        messages=messages, voice_minutes=voice, active_users=users,
        events=event_count or 0, parties=party_count or 0, tickets=ticket_count or 0,
    )


async def personal_totals(db, *, guild_id: int, user_id: int,
                          include_balance: bool) -> UserTotals:
    async with db.sessions() as session:
        progress = await session.scalar(select(MemberProgress).where(
            MemberProgress.guild_id == guild_id, MemberProgress.user_id == user_id,
        ))
        messages, voice = (await session.execute(select(
            func.coalesce(func.sum(StatsDay.messages), 0),
            func.coalesce(func.sum(StatsDay.voice_minutes), 0),
        ).where(StatsDay.guild_id == guild_id, StatsDay.user_id == user_id))).one()
        achievements = await session.scalar(select(func.count()).select_from(
            AchievementUnlock).where(
            AchievementUnlock.guild_id == guild_id, AchievementUnlock.user_id == user_id,
        ))
        quests = await session.scalar(select(func.count()).select_from(
            QuestClaim).where(QuestClaim.guild_id == guild_id, QuestClaim.user_id == user_id))
        events, attendance = (await session.execute(select(
            func.count(EventSignup.id),
            func.count(EventSignup.attended_at),
        ).join(CommunityEvent, CommunityEvent.id == EventSignup.event_id).where(
            CommunityEvent.guild_id == guild_id, EventSignup.user_id == user_id,
            EventSignup.status == "registered",
        ))).one()
        parties = await session.scalar(select(func.count()).select_from(
            PartyMember).join(PartyListing, PartyListing.id == PartyMember.party_id).where(
            PartyListing.guild_id == guild_id, PartyMember.user_id == user_id,
        ))
        profile_public = await session.scalar(select(MemberProfile.public).where(
            MemberProfile.guild_id == guild_id, MemberProfile.user_id == user_id))
        balance = None
        if include_balance:
            balance = await session.scalar(select(Wallet.balance).where(
                Wallet.guild_id == guild_id, Wallet.user_id == user_id))
    return UserTotals(
        level=progress.level if progress else 1,
        xp=progress.xp if progress else 0,
        credited_messages=progress.credited_messages if progress else 0,
        credited_voice_minutes=progress.active_voice_minutes if progress else 0,
        tracked_messages=messages, tracked_voice_minutes=voice,
        achievements=achievements or 0, quests=quests or 0,
        event_registrations=events, event_attendance=attendance,
        parties=parties or 0, wallet_balance=(balance or 0) if include_balance else None,
        public=profile_public is not False,
    )


async def activity_series(db, guild_id: int, period: Period) -> list[tuple[str, int, int, int, int]]:
    """Ежедневные агрегаты: максимум один ряд на день, не история сообщений."""
    async with db.sessions() as session:
        rows = (await session.execute(select(
            StatsDay.day, func.sum(StatsDay.messages), func.sum(StatsDay.voice_minutes),
        ).where(
            StatsDay.guild_id == guild_id,
            StatsDay.day.between(period.first, period.last),
        ).group_by(StatsDay.day).order_by(StatsDay.day))).all()
        members = (await session.execute(select(
            StatsServerDay.day, StatsServerDay.joins, StatsServerDay.leaves,
        ).where(
            StatsServerDay.guild_id == guild_id,
            StatsServerDay.day.between(period.first, period.last),
        ))).all()
    activity = {day: (int(messages), int(voice)) for day, messages, voice in rows}
    changes = {day: (joins, leaves) for day, joins, leaves in members}
    current = datetime.fromisoformat(period.first).date()
    last = datetime.fromisoformat(period.last).date()
    result = []
    while current <= last:
        day = current.isoformat()
        msg, voice = activity.get(day, (0, 0))
        joins, leaves = changes.get(day, (0, 0))
        result.append((day, msg, voice, joins, leaves))
        current += timedelta(days=1)
    return result


async def leaderboard(db, *, guild_id: int, period: Period, metric: str,
                      active_user_ids: set[int], limit: int = 10) -> list[tuple[int, int]]:
    if metric not in ("xp", "messages", "voice"):
        raise ValueError("Неизвестный показатель рейтинга.")
    if not 1 <= limit <= 25:
        raise ValueError("Лимит рейтинга 1–25.")
    if not active_user_ids:
        return []
    async with db.sessions() as session:
        if metric == "xp":
            values = (await session.execute(select(
                MemberProgress.user_id, MemberProgress.xp,
            ).where(
                MemberProgress.guild_id == guild_id,
                MemberProgress.xp > 0,
            ).order_by(MemberProgress.xp.desc(), MemberProgress.user_id))).all()
        else:
            column = StatsDay.messages if metric == "messages" else StatsDay.voice_minutes
            amount = func.sum(column).label("score")
            values = (await session.execute(select(
                StatsDay.user_id, amount,
            ).where(
                StatsDay.guild_id == guild_id,
                StatsDay.day.between(period.first, period.last),
            ).group_by(StatsDay.user_id).having(amount > 0).order_by(
                amount.desc(), StatsDay.user_id,
            ))).all()
        hidden = set((await session.scalars(select(MemberProfile.user_id).where(
            MemberProfile.guild_id == guild_id,
            MemberProfile.public.is_(False),
        ))).all())
    return [(uid, int(score)) for uid, score in values
            if uid in active_user_ids and uid not in hidden][:limit]


async def panels(db, guild_id: int) -> list[StatsPanel]:
    async with db.sessions() as session:
        return list((await session.scalars(select(StatsPanel).where(
            StatsPanel.guild_id == guild_id,
        ).order_by(StatsPanel.id))).all())


async def save_panel(db, *, guild_id: int, channel_id: int, message_id: int,
                     kind: str, now: int | None = None) -> None:
    if kind not in ("server", "activity"):
        raise ValueError("Неизвестный тип панели статистики.")
    now = int(time.time()) if now is None else now
    async with db.session() as session:
        await session.execute(sqlite_insert(StatsPanel).values(
            guild_id=guild_id, channel_id=channel_id,
            message_id=message_id, kind=kind, updated_at=now,
        ).on_conflict_do_update(
            index_elements=[StatsPanel.guild_id, StatsPanel.channel_id, StatsPanel.kind],
            set_={"message_id": message_id, "updated_at": now},
        ))


async def remove_panel(db, panel_id: int, guild_id: int) -> None:
    async with db.session() as session:
        await session.execute(delete(StatsPanel).where(
            StatsPanel.id == panel_id, StatsPanel.guild_id == guild_id,
        ))


async def mark_panel_updated(db, *, panel_id: int, guild_id: int,
                             now: int | None = None) -> None:
    now = int(time.time()) if now is None else now
    async with db.session() as session:
        await session.execute(update(StatsPanel).where(
            StatsPanel.id == panel_id, StatsPanel.guild_id == guild_id,
        ).values(updated_at=now))
