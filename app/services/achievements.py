"""Достижения без накрутки: проверка по фактической статистике и выдача наград один раз."""
from __future__ import annotations

import time
from dataclasses import dataclass

from sqlalchemy import func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.models import AchievementUnlock, CommunityEvent, EventSignup, ExperienceAward, MemberProgress, QuestClaim
from app.services import economy, progression


@dataclass(frozen=True)
class Achievement:
    code: str
    title: str
    description: str
    rarity: str
    metric: str
    goal: int
    xp: int
    coins: int
    hidden: bool = False


CATALOG: tuple[Achievement, ...] = (
    Achievement("first_words", "Первое слово", "Первое сообщение с XP", "common", "messages", 1, 15, 20),
    Achievement("active_talker", "Душа компании", "50 сообщений с XP", "rare", "messages", 50, 100, 100),
    Achievement("voice_hour", "На одной волне", "60 минут активного голосового общения", "uncommon", "voice", 60, 70, 70),
    Achievement("voice_veteran", "Ночная смена", "600 минут активного голосового общения", "epic", "voice", 600, 300, 350, True),
    Achievement("level_five", "Своя компания", "Достигнуть 5 уровня", "uncommon", "level", 5, 100, 80),
    Achievement("level_ten", "Ветеран NewSquad", "Достигнуть 10 уровня", "rare", "level", 10, 250, 250),
    Achievement("quests_five", "Исполнитель", "Получить 5 наград за задания", "rare", "quests", 5, 100, 120),
    Achievement("quests_twenty", "Надёжный человек", "Получить 20 наград за задания", "epic", "quests", 20, 300, 400, True),
    Achievement("first_event", "В центре событий", "Посетить первое подтверждённое мероприятие",
                "uncommon", "events", 1, 75, 90),
    Achievement("events_ten", "Легенда событий", "Посетить 10 подтверждённых мероприятий",
                "epic", "events", 10, 320, 360, True),
)
BY_CODE = {item.code: item for item in CATALOG}


@dataclass(frozen=True)
class AchievementState:
    achievement: Achievement
    value: int
    unlocked_at: int | None
    claimed_at: int | None


@dataclass(frozen=True)
class ClaimedReward:
    code: str
    xp: int
    coins: int
    level_before: int
    level_after: int


async def _metrics(session, guild_id: int, user_id: int) -> dict[str, int]:
    row = await session.scalar(select(MemberProgress).where(
        MemberProgress.guild_id == guild_id, MemberProgress.user_id == user_id))
    claims = await session.scalar(select(func.count(QuestClaim.id)).where(
        QuestClaim.guild_id == guild_id, QuestClaim.user_id == user_id))
    events = await session.scalar(
        select(func.count(EventSignup.id)).join(
            CommunityEvent, CommunityEvent.id == EventSignup.event_id).where(
            CommunityEvent.guild_id == guild_id, EventSignup.user_id == user_id,
            EventSignup.attended_at.is_not(None)))
    return {"messages": row.credited_messages if row else 0,
            "voice": row.active_voice_minutes if row else 0,
            "level": row.level if row else 1,
            "quests": int(claims or 0), "events": int(events or 0)}


async def sync(db, guild_id: int, user_id: int, *, now: int | None = None) -> list[str]:
    """Запоминает достижение навсегда; повторная доставка XP-события безопасна."""
    now = int(time.time()) if now is None else now
    unlocked: list[str] = []
    async with db.session() as session:
        metrics = await _metrics(session, guild_id, user_id)
        for item in CATALOG:
            if metrics[item.metric] < item.goal:
                continue
            result = await session.execute(sqlite_insert(AchievementUnlock).values(
                guild_id=guild_id, user_id=user_id, code=item.code,
                unlocked_at=now, claimed_at=None,
            ).on_conflict_do_nothing(index_elements=[AchievementUnlock.guild_id,
                                                      AchievementUnlock.user_id, AchievementUnlock.code]))
            if result.rowcount == 1:
                unlocked.append(item.code)
    return unlocked


async def list_achievements(db, guild_id: int, user_id: int) -> list[AchievementState]:
    async with db.sessions() as session:
        metrics = await _metrics(session, guild_id, user_id)
        unlocked = {r.code: r for r in (await session.scalars(select(AchievementUnlock).where(
            AchievementUnlock.guild_id == guild_id, AchievementUnlock.user_id == user_id))).all()}
        return [AchievementState(item, min(metrics[item.metric], item.goal),
                                 unlocked[item.code].unlocked_at if item.code in unlocked else None,
                                 unlocked[item.code].claimed_at if item.code in unlocked else None)
                for item in CATALOG]


async def claim(db, *, guild_id: int, user_id: int, code: str,
                timezone: str, now: int | None = None) -> ClaimedReward:
    now = int(time.time()) if now is None else now
    item = BY_CODE.get(code)
    if item is None:
        raise ValueError("Неизвестное достижение. Используйте /achievements list.")
    async with db.session() as session:
        metrics = await _metrics(session, guild_id, user_id)
        if metrics[item.metric] < item.goal:
            raise ValueError("Это достижение ещё не открыто.")
        # Выдача награды защищена атомарным условием claimed_at IS NULL.
        await session.execute(sqlite_insert(AchievementUnlock).values(
            guild_id=guild_id, user_id=user_id, code=code,
            unlocked_at=now, claimed_at=None,
        ).on_conflict_do_nothing(index_elements=[AchievementUnlock.guild_id,
                                                  AchievementUnlock.user_id, AchievementUnlock.code]))
        change = await session.execute(update(AchievementUnlock).where(
            AchievementUnlock.guild_id == guild_id, AchievementUnlock.user_id == user_id,
            AchievementUnlock.code == code, AchievementUnlock.claimed_at.is_(None)
        ).values(claimed_at=now))
        if change.rowcount != 1:
            raise ValueError("Награда за это достижение уже получена.")
        row = await progression._progress(session, guild_id, user_id, now)
        progression._roll_day(row, progression.day_key(now, timezone))
        award = await progression._credit(session, row, amount=item.xp,
            source="achievement", reference=code, now=now, cap=row.daily_xp + item.xp)
        session.add(ExperienceAward(guild_id=guild_id, user_id=user_id,
                                    source="achievement", reference=code,
                                    amount=award.amount, created_at=now))
        await economy._apply(session, guild_id=guild_id, user_id=user_id,
                             amount=item.coins, kind="achievement",
                             reference=f"achievement:{user_id}:{code}", actor_id=user_id, now=now)
        return ClaimedReward(code, award.amount, item.coins, award.level_before, award.level_after)
