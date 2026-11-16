"""Накопление XP: одна транзакция на начисление, антифлуд и атомарные события роста уровня."""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from math import isqrt
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.models import ExperienceAward, MemberProgress, OutboxEvent

MAX_XP = 10**12
MESSAGE_MIN_LENGTH = 10
VOICE_REWARD_INTERVAL = 10
VOICE_REWARD_XP = 30
MESSAGE_REWARD_XP = 20
DEFAULT_DAILY_CAP = 1200
DEFAULT_COOLDOWN = 60


@dataclass(frozen=True)
class AwardResult:
    amount: int
    xp: int
    level_before: int
    level_after: int
    reason: str = ""


def threshold(level: int) -> int:
    """XP от начала прогресса для уровня L (уровень 1 начинается с нуля)."""
    if level < 1:
        raise ValueError("Уровень должен быть положительным")
    return 100 * (level - 1) * level


def level_for_xp(xp: int) -> int:
    if xp < 0:
        raise ValueError("XP не может быть отрицательным")
    q = xp // 100
    return (1 + isqrt(1 + 4 * q)) // 2


def progress_bar(xp: int, width: int = 12) -> str:
    level = level_for_xp(xp)
    current, following = threshold(level), threshold(level + 1)
    filled = max(0, min(width, (xp - current) * width // (following - current)))
    return "▰" * filled + "▱" * (width - filled)


def normalize_message(content: str) -> str:
    plain = re.sub(r"https?://\S+", "", content, flags=re.I)
    plain = " ".join(plain.casefold().split())
    return plain


def valid_message(content: str) -> bool:
    text = normalize_message(content)
    return len(text) >= MESSAGE_MIN_LENGTH and sum(c.isalnum() for c in text) >= 8


def day_key(timestamp: int, timezone: str) -> str:
    return datetime.fromtimestamp(timestamp, ZoneInfo(timezone)).date().isoformat()


async def get_progress(db, guild_id: int, user_id: int) -> MemberProgress | None:
    async with db.sessions() as session:
        return await session.scalar(select(MemberProgress).where(
            MemberProgress.guild_id == guild_id, MemberProgress.user_id == user_id))


async def top(db, guild_id: int, limit: int = 10) -> list[MemberProgress]:
    if not 1 <= limit <= 25:
        raise ValueError("Ограничение рейтинга: 1–25")
    async with db.sessions() as session:
        return list((await session.scalars(select(MemberProgress).where(
            MemberProgress.guild_id == guild_id, MemberProgress.xp > 0
        ).order_by(MemberProgress.xp.desc(), MemberProgress.user_id.asc()).limit(limit))).all())


async def _progress(session, guild_id: int, user_id: int, now: int) -> MemberProgress:
    stmt = sqlite_insert(MemberProgress).values(
        guild_id=guild_id, user_id=user_id, xp=0, level=1, credited_messages=0,
        active_voice_minutes=0, voice_bank_minutes=0, last_message_at=0,
        last_message_hash="", last_voice_minute=-1, daily_key="", daily_xp=0,
        created_at=now, updated_at=now,
    ).on_conflict_do_nothing(index_elements=[MemberProgress.guild_id, MemberProgress.user_id])
    await session.execute(stmt)
    return await session.scalar(select(MemberProgress).where(
        MemberProgress.guild_id == guild_id, MemberProgress.user_id == user_id))


def _roll_day(row: MemberProgress, day: str) -> None:
    if row.daily_key != day:
        row.daily_key = day
        row.daily_xp = 0


async def _credit(session, row: MemberProgress, *, amount: int, source: str, reference: str,
                  now: int, cap: int) -> AwardResult:
    previous = row.level
    amount = min(amount, max(0, cap - row.daily_xp), max(0, MAX_XP - row.xp))
    if amount <= 0:
        return AwardResult(0, row.xp, previous, previous, "Достигнут дневной лимит XP")
    old_xp = row.xp
    row.xp = old_xp + amount
    row.daily_xp += amount
    row.level = level_for_xp(row.xp)
    row.updated_at = now
    for level in range(previous + 1, row.level + 1):
        key = f"lvl:{row.guild_id}:{row.user_id}:{level}"
        event = sqlite_insert(OutboxEvent).values(
            id=key, kind="leveling.level_up",
            payload_json=json.dumps({"guild_id": row.guild_id, "user_id": row.user_id, "level": level}),
            created_at=now,
        ).on_conflict_do_nothing(index_elements=[OutboxEvent.id])
        await session.execute(event)
    return AwardResult(amount, row.xp, previous, row.level)


async def award_message(db, *, guild_id: int, user_id: int, message_id: int, content: str,
                        now: int | None = None, timezone: str = "Europe/Tallinn",
                        cooldown: int = DEFAULT_COOLDOWN, amount: int = MESSAGE_REWARD_XP,
                        daily_cap: int = DEFAULT_DAILY_CAP) -> AwardResult:
    now = int(time.time()) if now is None else now
    if not valid_message(content):
        return AwardResult(0, 0, 1, 1, "Сообщение слишком короткое или не содержит текста")
    if not 0 <= cooldown <= 3600 or not 1 <= amount <= 100 or not 1 <= daily_cap <= 20000:
        raise ValueError("Некорректные параметры начисления XP")
    digest = hashlib.sha256(normalize_message(content).encode("utf-8")).hexdigest()
    async with db.session() as session:
        row = await _progress(session, guild_id, user_id, now)
        _roll_day(row, day_key(now, timezone))
        receipt = sqlite_insert(ExperienceAward).values(
            guild_id=guild_id, user_id=user_id, source="message", reference=str(message_id),
            amount=0, created_at=now,
        ).on_conflict_do_nothing(index_elements=[
            ExperienceAward.guild_id, ExperienceAward.user_id,
            ExperienceAward.source, ExperienceAward.reference,
        ])
        inserted = await session.execute(receipt)
        if inserted.rowcount != 1:
            return AwardResult(0, row.xp, row.level, row.level, "Сообщение уже учтено")
        if now - row.last_message_at < cooldown or digest == row.last_message_hash:
            return AwardResult(0, row.xp, row.level, row.level, "Антифлуд")
        result = await _credit(session, row, amount=amount, source="message", reference=str(message_id),
                               now=now, cap=daily_cap)
        if result.amount:
            row.last_message_at = now
            row.last_message_hash = digest
            row.credited_messages += 1
            await session.execute(
                ExperienceAward.__table__.update().where(
                    ExperienceAward.guild_id == guild_id,
                    ExperienceAward.user_id == user_id,
                    ExperienceAward.source == "message",
                    ExperienceAward.reference == str(message_id),
                ).values(amount=result.amount)
            )
        return result


async def record_voice_minute(db, *, guild_id: int, user_id: int, minute: int,
                              timezone: str = "Europe/Tallinn", reward_xp: int = VOICE_REWARD_XP,
                              daily_cap: int = DEFAULT_DAILY_CAP) -> AwardResult:
    if not 1 <= reward_xp <= 200 or not 1 <= daily_cap <= 20000:
        raise ValueError("Некорректные параметры голосового XP")
    now = minute * 60
    async with db.session() as session:
        row = await _progress(session, guild_id, user_id, now)
        if row.last_voice_minute >= minute:
            return AwardResult(0, row.xp, row.level, row.level, "Минута уже учтена")
        row.last_voice_minute = minute
        row.active_voice_minutes += 1
        row.voice_bank_minutes += 1
        row.updated_at = now
        _roll_day(row, day_key(now, timezone))
        if row.voice_bank_minutes < VOICE_REWARD_INTERVAL:
            return AwardResult(0, row.xp, row.level, row.level)
        row.voice_bank_minutes -= VOICE_REWARD_INTERVAL
        result = await _credit(session, row, amount=reward_xp, source="voice",
                               reference=str(minute), now=now, cap=daily_cap)
        if result.amount:
            session.add(ExperienceAward(guild_id=guild_id, user_id=user_id,
                                        source="voice", reference=str(minute),
                                        amount=result.amount, created_at=now))
        return result
