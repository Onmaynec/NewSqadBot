"""Privacy-first support satisfaction surveys.

Only a 1–5 score and response latency survive closing a ticket. The closed
ticket, its messages, subject, description and all transcript content are erased.
"""
from __future__ import annotations

import time

from sqlalchemy import case, func, select, update

from app.models import SupportSurvey

SURVEY_SECONDS = 7 * 86400


async def pending(db, guild_id: int, *, requester_id: int | None = None,
                  unsent_only: bool = False, limit: int = 1000, now: int | None = None):
    now = int(time.time()) if now is None else now
    async with db.sessions() as session:
        query = select(SupportSurvey).where(
            SupportSurvey.guild_id == guild_id,
            SupportSurvey.expires_at > now,
            SupportSurvey.rating.is_(None),
            SupportSurvey.requester_id > 0,
        )
        if requester_id is not None:
            query = query.where(SupportSurvey.requester_id == requester_id)
        if unsent_only:
            query = query.where(SupportSurvey.notified_at.is_(None))
        return list((await session.scalars(
            query.order_by(SupportSurvey.id.desc()).limit(limit))).all())


async def mark_sent(db, guild_id: int, survey_id: int, *, now: int | None = None) -> bool:
    now = int(time.time()) if now is None else now
    async with db.session() as session:
        result = await session.execute(update(SupportSurvey).where(
            SupportSurvey.guild_id == guild_id, SupportSurvey.id == survey_id,
            SupportSurvey.notified_at.is_(None), SupportSurvey.rating.is_(None))
            .values(notified_at=now))
        return result.rowcount == 1


async def rate(db, guild_id: int, survey_id: int, requester_id: int,
               score: int, *, now: int | None = None) -> bool:
    """One person, one score, expiry enforced atomically, no free-text review."""
    now = int(time.time()) if now is None else now
    if type(score) is not int or not 1 <= score <= 5:
        raise ValueError("Оценка должна быть от 1 до 5.")
    async with db.session() as session:
        result = await session.execute(update(SupportSurvey).where(
            SupportSurvey.guild_id == guild_id, SupportSurvey.id == survey_id,
            SupportSurvey.requester_id == requester_id,
            SupportSurvey.expires_at > now, SupportSurvey.rating.is_(None))
            .values(rating=score, rated_at=now, requester_id=0))
        return result.rowcount == 1


async def summary(db, guild_id: int, *, days: int = 30, now: int | None = None):
    now = int(time.time()) if now is None else now
    start = now - days * 86400
    async with db.sessions() as session:
        aggregate = await session.execute(select(
            func.count(SupportSurvey.id),
            func.count(SupportSurvey.rating),
            func.avg(SupportSurvey.rating),
            func.count(SupportSurvey.response_seconds),
            func.avg(SupportSurvey.response_seconds),
            func.sum(case((SupportSurvey.rating >= 4, 1), else_=0)),
        ).where(SupportSurvey.guild_id == guild_id, SupportSurvey.created_at >= start))
        closed, total, average, answered, response, positive = aggregate.one()
        by_staff = (await session.execute(select(
            SupportSurvey.staff_id,
            func.count(SupportSurvey.id),
            func.count(SupportSurvey.rating),
            func.avg(SupportSurvey.rating),
            func.count(SupportSurvey.response_seconds),
            func.avg(SupportSurvey.response_seconds),
        ).where(SupportSurvey.guild_id == guild_id, SupportSurvey.created_at >= start)
            .group_by(SupportSurvey.staff_id)
            .order_by(func.count(SupportSurvey.id).desc()).limit(40))).all()
    return {
        "period_days": days,
        "closed_tickets": int(closed or 0),
        "ratings": int(total or 0),
        "answered_tickets": int(answered or 0),
        "average_rating": round(float(average), 2) if average is not None else None,
        "positive_percent": round(100 * int(positive or 0) / int(total), 1) if total else None,
        "avg_first_response_seconds": round(float(response)) if response is not None else None,
        "staff": [{"user_id": str(uid), "closed_tickets": closed_count, "ratings": rated,
                   "answered_tickets": responses,
                   "average_rating": round(float(avg), 2) if avg is not None else None,
                   "avg_first_response_seconds": round(float(delay)) if delay is not None else None}
                  for uid, closed_count, rated, avg, responses, delay in by_staff],
    }


async def purge(db, guild_id: int, *, now: int | None = None) -> None:
    """Invalidate requester references for unanswered expired surveys; no data linkage."""
    now = int(time.time()) if now is None else now
    async with db.session() as session:
        from sqlalchemy import delete

        await session.execute(delete(SupportSurvey).where(
            SupportSurvey.guild_id == guild_id, SupportSurvey.rating.is_(None),
            SupportSurvey.expires_at <= now))
