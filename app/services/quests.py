"""Задания NewSquad: прогресс только по засчитанной активности, без фонового сброса."""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime, time as day_time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.models import ExperienceAward, QuestClaim
from app.services import economy, progression


@dataclass(frozen=True)
class Quest:
    code: str
    title: str
    description: str
    frequency: str
    metric: str
    goal: int
    xp: int
    coins: int


# Состав заданий фиксируется кодом релиза. Не меняем коды существующих целей:
# прошлые квитанции должны переживать обновление программы.
CATALOG: tuple[Quest, ...] = (
    Quest("daily_talk", "Первый контакт", "3 сообщения с начисленным XP", "daily", "messages", 3, 35, 25),
    Quest("daily_voice", "На одной волне", "20 минут совместного голосового общения", "daily", "voice", 20, 50, 35),
    Quest("daily_chat", "На связи", "8 сообщений с начисленным XP", "daily", "messages", 8, 75, 50),
    Quest("weekly_talk", "Активный участник", "25 сообщений с начисленным XP", "weekly", "messages", 25, 180, 150),
    Quest("weekly_voice", "Собираем команду", "60 минут совместного голосового общения", "weekly", "voice", 60, 230, 200),
    Quest("weekly_daily", "Привычка общаться", "Получить 4 ежедневные награды за задания", "weekly", "daily_claims", 4, 250, 175),
)
BY_CODE = {item.code: item for item in CATALOG}


@dataclass(frozen=True)
class QuestState:
    quest: Quest
    value: int
    claimed: bool
    period: str
    ends_at: int


@dataclass(frozen=True)
class ClaimedReward:
    code: str
    xp: int
    coins: int
    level_before: int
    level_after: int


def period_bounds(now: int, timezone: str, frequency: str) -> tuple[str, int, int]:
    zone = ZoneInfo(timezone)
    today = datetime.fromtimestamp(now, zone).date()
    if frequency == "daily":
        begin, end = today, today + timedelta(days=1)
    elif frequency == "weekly":
        begin = today - timedelta(days=today.weekday())
        end = begin + timedelta(days=7)
    else:
        raise ValueError("Частота должна быть daily или weekly")

    def stamp(day: date) -> int:
        return int(datetime.combine(day, day_time.min, zone).timestamp())

    return begin.isoformat(), stamp(begin), stamp(end)


async def _counts(session, *, guild_id: int, user_id: int, start: int, end: int) -> dict[str, int]:
    rows = (await session.execute(
        select(ExperienceAward.source, func.count(ExperienceAward.id))
        .where(ExperienceAward.guild_id == guild_id,
               ExperienceAward.user_id == user_id,
               ExperienceAward.source.in_(("message", "voice")),
               ExperienceAward.amount > 0,
               ExperienceAward.created_at >= start, ExperienceAward.created_at < end)
        .group_by(ExperienceAward.source)
    )).all()
    by_source = dict(rows)
    claimed_daily = await session.scalar(
        select(func.count(QuestClaim.id)).where(
            QuestClaim.guild_id == guild_id, QuestClaim.user_id == user_id,
            QuestClaim.frequency == "daily",
            QuestClaim.claimed_at >= start, QuestClaim.claimed_at < end,
        )
    )
    return {"messages": int(by_source.get("message", 0)),
            "voice": int(by_source.get("voice", 0)) * progression.VOICE_REWARD_INTERVAL,
            "daily_claims": int(claimed_daily or 0)}


async def list_quests(db, guild_id: int, user_id: int, timezone: str, frequency: str,
                      now: int | None = None) -> list[QuestState]:
    now = int(time.time()) if now is None else now
    period, start, end = period_bounds(now, timezone, frequency)
    async with db.sessions() as session:
        counts = await _counts(session, guild_id=guild_id, user_id=user_id, start=start, end=end)
        claimed = set((await session.scalars(select(QuestClaim.code).where(
            QuestClaim.guild_id == guild_id, QuestClaim.user_id == user_id,
            QuestClaim.frequency == frequency, QuestClaim.period == period))).all())
    return [QuestState(item, min(counts[item.metric], item.goal), item.code in claimed, period, end)
            for item in CATALOG if item.frequency == frequency]


async def claim(db, *, guild_id: int, user_id: int, code: str, timezone: str,
                now: int | None = None) -> ClaimedReward:
    """Списание/выплаты/XP/квитанция в одной транзакции SQLite."""
    now = int(time.time()) if now is None else now
    quest = BY_CODE.get(code)
    if quest is None:
        raise ValueError("Неизвестное задание. Используйте /quests daily или /quests weekly.")
    period, start, end = period_bounds(now, timezone, quest.frequency)
    async with db.session() as session:
        counts = await _counts(session, guild_id=guild_id, user_id=user_id, start=start, end=end)
        if counts[quest.metric] < quest.goal:
            raise ValueError(f"Задание ещё не выполнено: {counts[quest.metric]}/{quest.goal}.")
        receipt = await session.execute(sqlite_insert(QuestClaim).values(
            guild_id=guild_id, user_id=user_id, code=code, period=period,
            frequency=quest.frequency, xp_reward=quest.xp, coin_reward=quest.coins,
            claimed_at=now,
        ).on_conflict_do_nothing(index_elements=[QuestClaim.guild_id, QuestClaim.user_id,
                                                  QuestClaim.code, QuestClaim.period]))
        if receipt.rowcount != 1:
            raise ValueError("Награда за это задание уже получена.")
        xp_row = await progression._progress(session, guild_id, user_id, now)
        progression._roll_day(xp_row, progression.day_key(now, timezone))
        xp_result = await progression._credit(session, xp_row, amount=quest.xp,
            source="quest", reference=f"{code}:{period}", now=now,
            cap=xp_row.daily_xp + quest.xp)
        session.add(ExperienceAward(guild_id=guild_id, user_id=user_id, source="quest",
                                    reference=f"{code}:{period}", amount=xp_result.amount, created_at=now))
        await economy._apply(session, guild_id=guild_id, user_id=user_id, amount=quest.coins,
                             kind="quest", reference=f"quest:{user_id}:{code}:{period}",
                             actor_id=user_id, now=now)
        return ClaimedReward(code, xp_result.amount, quest.coins,
                             xp_result.level_before, xp_result.level_after)


async def history(db, guild_id: int, user_id: int, limit: int = 15) -> list[QuestClaim]:
    async with db.sessions() as session:
        return list((await session.scalars(select(QuestClaim).where(
            QuestClaim.guild_id == guild_id, QuestClaim.user_id == user_id)
            .order_by(QuestClaim.claimed_at.desc(), QuestClaim.id.desc()).limit(limit))).all())
