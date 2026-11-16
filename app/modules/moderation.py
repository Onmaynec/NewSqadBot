"""Ручная модерация: короткая /m, собственный бан-лист и история дел."""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.durations import parse_duration
from app.core.permissions import can_manage_settings
from app.models import ModerationAction, ModerationCase
from app.services.audit import log_event
from app.ui.theme import BRAND_BLUE, LoggedView

logger = logging.getLogger(__name__)


def audit_reason(case_id: int, reason: str) -> str:
    return f"[NSB-{case_id:05d}] {reason}"[:512]


class Moderation(commands.Cog):
    m = app_commands.Group(name="m", description="Модерация NewSquad")

    def __init__(self, bot):
        self.bot = bot
        self.bot.scheduler.register("moderation.unban", self._expire_ban)

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        # Recover deadlines even if the process was interrupted between ban and scheduling.
        async with self.bot.database.sessions() as session:
            cases = (await session.scalars(select(ModerationCase).where(
                ModerationCase.guild_id == self.bot.config.guild_id,
                ModerationCase.kind == "ban", ModerationCase.status.in_(("active", "pending"))
            ))).all()
        guild = self.bot.get_guild(self.bot.config.guild_id)
        if not guild:
            return
        for case in cases:
            try:
                current = await guild.fetch_ban(discord.Object(id=case.user_id))
            except discord.NotFound:
                await self.update_case(case.id, status="failed" if case.status == "pending" else "removed",
                                       error="Нет соответствующего Discord-бана")
                continue
            except discord.HTTPException:
                logger.exception("Ошибка сверки бана #%s", case.id)
                continue
            if not (current.reason or "").startswith(f"[NSB-{case.id:05d}]"):
                await self.update_case(case.id, status="superseded", error="Бан заменён другим решением")
                continue
            if case.status == "pending":
                await self.update_case(case.id, status="active")
            if case.expires_at:
                await self.bot.scheduler.schedule(job_key=f"moderation:unban:{case.id}",
                    handler="moderation.unban", due_at=case.expires_at, payload={"case_id": case.id})

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User) -> None:
        if guild.id != self.bot.config.guild_id:
            return
        await asyncio.sleep(1)
        async with self.bot.database.sessions() as session:
            existing = await session.scalar(select(ModerationCase.id).where(
                ModerationCase.guild_id == guild.id, ModerationCase.user_id == user.id,
                ModerationCase.kind == "ban", ModerationCase.status.in_(("pending", "active", "external_active"))).limit(1))
        if existing:
            return
        try:
            entry = await guild.fetch_ban(user)
        except discord.HTTPException:
            return
        case = await self.new_case(user.id, 0, "ban", entry.reason or "Бан выдан вне NewSqadBot")
        await self.update_case(case.id, status="external_active", dm_status="n_a")
        await self.report(await self.get_case(case.id), "Внешний бан Discord")

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User) -> None:
        if guild.id != self.bot.config.guild_id:
            return
        async with self.bot.database.sessions() as session:
            active = (await session.scalars(select(ModerationCase).where(
                ModerationCase.guild_id == guild.id, ModerationCase.user_id == user.id,
                ModerationCase.kind == "ban", ModerationCase.status.in_(("active", "external_active"))))).all()
        for case in active:
            await self.update_case(case.id, status="removed", closed_by=0)

    async def can_moderate(self, interaction: discord.Interaction, *, action: str | None = None) -> bool:
        if interaction.guild_id != self.bot.config.guild_id:
            return False
        if can_manage_settings(interaction, self.bot.config.owner_ids):
            return True
        role_id = await self.bot.database.get_setting(interaction.guild_id, "moderator_role_id")
        return bool(role_id and isinstance(interaction.user, discord.Member)
                    and any(role.id == role_id for role in interaction.user.roles))

    async def guard(self, interaction: discord.Interaction, target: discord.User | discord.Member | None = None) -> bool:
        if not await self.can_moderate(interaction):
            await interaction.response.send_message("Нет прав модератора NewSquad.", ephemeral=True)
            return False
        if target is not None:
            member = interaction.guild.get_member(target.id)
            if target.id == interaction.guild.owner_id or target.id == interaction.user.id or (
                member and (member.guild_permissions.administrator or
                            member.top_role >= interaction.user.top_role and not can_manage_settings(interaction, self.bot.config.owner_ids))
            ):
                await interaction.response.send_message("Нельзя наказать этого участника.", ephemeral=True)
                return False
        return True

    async def new_case(self, target_id: int, moderator_id: int, kind: str, reason: str,
                       expires_at: int | None = None) -> ModerationCase:
        try:
            async with self.bot.database.session() as session:
                case = ModerationCase(guild_id=self.bot.config.guild_id, user_id=target_id,
                    moderator_id=moderator_id, kind=kind, reason=reason[:400], status="pending",
                    created_at=int(time.time()), expires_at=expires_at)
                session.add(case)
                await session.flush()
                case_id = case.id
        except IntegrityError as exc:
            raise ValueError("Для участника уже есть активная блокировка, выданная ботом.") from exc
        return await self.get_case(case_id)

    async def get_case(self, case_id: int):
        async with self.bot.database.sessions() as session:
            return await session.get(ModerationCase, case_id)

    async def update_case(self, case_id: int, *, status: str | None = None,
                          dm_status: str | None = None, error: str | None = None, closed_by: int | None = None):
        async with self.bot.database.session() as session:
            row = await session.get(ModerationCase, case_id)
            if not row:
                return
            if status:
                row.status = status
                if status in ("expired", "revoked", "failed", "completed", "removed", "superseded"):
                    row.completed_at = int(time.time())
                    row.closed_by = closed_by
            if dm_status:
                row.dm_status = dm_status
            row.error = error[:500] if error else None
            session.add(ModerationAction(case_id=case_id, actor_id=closed_by or row.moderator_id,
                action=status or "dm", details=error or "", created_at=int(time.time())))

    async def notify(self, user: discord.abc.User, message: str, case_id: int) -> None:
        try:
            await user.send(embed=discord.Embed(
                title="NewSquad  /  Решение модерации",
                description=discord.utils.escape_mentions(message[:3500]),
                color=BRAND_BLUE).set_footer(text=f"NewSquad • Дело #{case_id:05d}"),
                allowed_mentions=discord.AllowedMentions.none())
            await self.update_case(case_id, dm_status="done")
        except discord.HTTPException:
            await self.update_case(case_id, dm_status="failed")

    async def report(self, case, title: str):
        await log_event(self.bot, channel_setting="audit_channel_id", title=title,
            details=f"Дело: #{case.id:05d}\nУчастник: {case.user_id}\nМодератор: {case.moderator_id}\n"
                    f"Причина: {discord.utils.escape_mentions(case.reason)}\nСтатус: {case.status}",
            actor_id=case.moderator_id, action=f"moderation.{case.kind}", target_id=case.user_id)

    async def _expire_ban(self, data: dict) -> None:
        case_id = int(data["case_id"])
        case = await self.get_case(case_id)
        if not case or case.guild_id != self.bot.config.guild_id or case.status != "active" or case.kind != "ban":
            return
        if case.expires_at and case.expires_at > time.time():
            # Clock changed; do not prematurely revoke.
            raise RuntimeError("Срок ещё не истёк")
        guild = self.bot.get_guild(self.bot.config.guild_id)
        if guild is None:
            raise RuntimeError("NewSquad недоступен")
        try:
            existing = await guild.fetch_ban(discord.Object(id=case.user_id))
        except discord.NotFound:
            await self.update_case(case_id, status="removed", closed_by=0)
            return
        if not (existing.reason or "").startswith(f"[NSB-{case_id:05d}]"):
            # A newer manual or external ban is NOT ours to revoke.
            await self.update_case(case_id, status="superseded", closed_by=0)
            return
        await guild.unban(discord.Object(id=case.user_id), reason=f"[NSB-{case_id:05d}] Срок блокировки истёк")
        await self.update_case(case_id, status="expired", closed_by=0)
        await self.report(await self.get_case(case_id), "Временный бан завершён")

    async def _ban(self, interaction: discord.Interaction, user: discord.User,
                   duration: str, reason: str | None) -> str:
        try:
            seconds = parse_duration(duration)
        except ValueError as exc:
            return str(exc)
        reason = (reason or "Причина не указана").strip()[:400]
        try:
            case = await self.new_case(user.id, interaction.user.id, "ban", reason,
                None if seconds is None else int(time.time()) + seconds)
        except ValueError as exc:
            return str(exc)
        text = f"Решение о блокировке на NewSquad.\nСрок: {duration}\nПричина: {reason}\nДело: #{case.id:05d}."
        await self.notify(user, text, case.id)
        try:
            await interaction.guild.ban(user, reason=audit_reason(case.id, reason))
        except discord.HTTPException as exc:
            await self.update_case(case.id, status="failed", error=str(exc))
            return f"Бан не выполнен: {exc}. Дело #{case.id:05d} сохранено."
        await self.update_case(case.id, status="active")
        if seconds is not None:
            await self.bot.scheduler.schedule(job_key=f"moderation:unban:{case.id}", handler="moderation.unban",
                due_at=case.expires_at, payload={"case_id": case.id})
        await self.report(await self.get_case(case.id), "Новый бан NewSquad")
        return f"Блокировка выполнена. Дело #{case.id:05d}; срок: {duration}; причина: {reason}."

    @m.command(name="ban", description="Заблокировать пользователя на срок или per")
    async def ban(self, interaction: discord.Interaction, user: discord.User,
                  duration: str, reason: str | None = None):
        if not await self.guard(interaction, user):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await self._ban(interaction, user, duration, reason)
        await interaction.followup.send(result, ephemeral=True)

    @m.command(name="unban", description="Разблокировать пользователя по Discord ID")
    async def unban(self, interaction: discord.Interaction, user_id: str, reason: str | None = None):
        if not await self.guard(interaction):
            return
        if not user_id.isascii() or not user_id.isdecimal():
            await interaction.response.send_message("Нужен цифровой Discord ID.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await interaction.guild.unban(discord.Object(id=int(user_id)),
                reason=(reason or "Снято модератором")[:400])
        except discord.HTTPException as exc:
            await interaction.followup.send(f"Не удалось снять бан: {exc}", ephemeral=True)
            return
        async with self.bot.database.sessions() as session:
            records = (await session.scalars(select(ModerationCase).where(
                ModerationCase.guild_id == interaction.guild_id, ModerationCase.user_id == int(user_id),
                ModerationCase.kind == "ban", ModerationCase.status == "active"))).all()
        for case in records:
            await self.update_case(case.id, status="revoked", closed_by=interaction.user.id)
            await self.report(await self.get_case(case.id), "Бан снят")
        await interaction.followup.send("Бан снят. История сохранена.", ephemeral=True)

    @m.command(name="kick", description="Исключить участника с сервера")
    async def kick(self, interaction: discord.Interaction, user: discord.Member, reason: str | None = None):
        if not await self.guard(interaction, user):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        case = await self.new_case(user.id, interaction.user.id, "kick", reason or "Причина не указана")
        await self.notify(user, f"Вас исключили из NewSquad.\nПричина: {case.reason}\nДело: #{case.id:05d}", case.id)
        try:
            await user.kick(reason=audit_reason(case.id, case.reason))
        except discord.HTTPException as exc:
            await self.update_case(case.id, status="failed", error=str(exc))
            await interaction.followup.send(f"Исключить не удалось: {exc}", ephemeral=True)
            return
        await self.update_case(case.id, status="completed")
        await self.report(await self.get_case(case.id), "Исключение")
        await interaction.followup.send(f"Участник исключён. Дело #{case.id:05d}.", ephemeral=True)

    @m.command(name="mute", description="Выдать тайм-аут (максимум 28 дней)")
    async def mute(self, interaction: discord.Interaction, user: discord.Member,
                   duration: str, reason: str | None = None):
        if not await self.guard(interaction, user):
            return
        try:
            seconds = parse_duration(duration, allow_permanent=False, max_seconds=28*86400)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        case = await self.new_case(user.id, interaction.user.id, "mute",
            reason or "Причина не указана", int(time.time()) + seconds)
        await self.notify(user, f"Вам выдан тайм-аут на NewSquad: {duration}.\nПричина: {case.reason}", case.id)
        try:
            await user.timeout(dt.timedelta(seconds=seconds), reason=audit_reason(case.id, case.reason))
        except discord.HTTPException as exc:
            await self.update_case(case.id, status="failed", error=str(exc))
            await interaction.followup.send(f"Тайм-аут не выполнен: {exc}", ephemeral=True)
            return
        await self.update_case(case.id, status="active")
        await self.report(await self.get_case(case.id), "Тайм-аут")
        await interaction.followup.send(f"Тайм-аут выдан. Дело #{case.id:05d}.", ephemeral=True)

    @m.command(name="unmute", description="Снять тайм-аут с участника")
    async def unmute(self, interaction: discord.Interaction, user: discord.Member, reason: str | None = None):
        if not await self.guard(interaction, user):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await user.timeout(None, reason=(reason or "Тайм-аут снят")[:400])
        except discord.HTTPException as exc:
            await interaction.followup.send(f"Не удалось снять тайм-аут: {exc}", ephemeral=True)
            return
        async with self.bot.database.sessions() as session:
            rows = (await session.scalars(select(ModerationCase).where(
                ModerationCase.guild_id == interaction.guild_id, ModerationCase.user_id == user.id,
                ModerationCase.kind == "mute", ModerationCase.status == "active"))).all()
        for row in rows:
            await self.update_case(row.id, status="revoked", closed_by=interaction.user.id)
        await interaction.followup.send("Тайм-аут снят.", ephemeral=True)

    @m.command(name="warn", description="Записать предупреждение")
    async def warn(self, interaction: discord.Interaction, user: discord.Member, reason: str | None = None):
        if not await self.guard(interaction, user):
            return
        case = await self.new_case(user.id, interaction.user.id, "warn", reason or "Причина не указана")
        await self.update_case(case.id, status="active")
        await self.notify(user, f"Вы получили предупреждение на NewSquad.\nПричина: {case.reason}\nДело: #{case.id:05d}", case.id)
        await self.report(await self.get_case(case.id), "Предупреждение")
        await interaction.response.send_message(f"Предупреждение #{case.id:05d} выдано.", ephemeral=True)

    @m.command(name="warnings", description="Посмотреть предупреждения участника")
    async def warnings(self, interaction: discord.Interaction, user: discord.User):
        if not await self.guard(interaction):
            return
        async with self.bot.database.sessions() as session:
            rows = (await session.scalars(select(ModerationCase).where(
                ModerationCase.guild_id == interaction.guild_id, ModerationCase.user_id == user.id,
                ModerationCase.kind == "warn").order_by(ModerationCase.id.desc()).limit(20))).all()
        lines = [f"#{r.id} [{r.status}]: {discord.utils.escape_mentions(r.reason)}" for r in rows]
        await interaction.response.send_message("\n".join(lines)[:1800] or "Предупреждений нет.", ephemeral=True,
                                                allowed_mentions=discord.AllowedMentions.none())

    @m.command(name="clearwarns", description="Снять действующие предупреждения")
    async def clearwarns(self, interaction: discord.Interaction, user: discord.User, reason: str | None = None):
        if not await self.guard(interaction):
            return
        async with self.bot.database.sessions() as session:
            rows = (await session.scalars(select(ModerationCase).where(
                ModerationCase.guild_id == interaction.guild_id, ModerationCase.user_id == user.id,
                ModerationCase.kind == "warn", ModerationCase.status == "active"))).all()
        for row in rows:
            await self.update_case(row.id, status="revoked", closed_by=interaction.user.id)
        await log_event(self.bot, channel_setting="audit_channel_id", title="Предупреждения сняты",
            details=f"Пользователь: {user.id}; снято: {len(rows)}; причина: {(reason or 'Не указана')[:300]}",
            actor_id=interaction.user.id, action="moderation.clearwarns", target_id=user.id)
        await interaction.response.send_message(f"Снято предупреждений: {len(rows)}.", ephemeral=True)

    @m.command(name="banlist", description="Список действующих блокировок NewSqadBot")
    async def banlist(self, interaction: discord.Interaction):
        if not await self.guard(interaction):
            return
        async with self.bot.database.sessions() as session:
            rows = (await session.scalars(select(ModerationCase).where(
                ModerationCase.guild_id == interaction.guild_id, ModerationCase.kind == "ban",
                ModerationCase.status == "active").order_by(ModerationCase.id.desc()).limit(25))).all()
        lines = [f"#{r.id}: `{r.user_id}` — {discord.utils.escape_mentions(r.reason[:90])} — "
                 f"{'per' if r.expires_at is None else f'<t:{r.expires_at}:R>'}" for r in rows]
        await interaction.response.send_message("\n".join(lines)[:1900] or "Активных банов бота нет.",
                                                ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @m.command(name="baninfo", description="Подробности дела по номеру")
    async def baninfo(self, interaction: discord.Interaction, number: int):
        if not await self.guard(interaction):
            return
        case = await self.get_case(number)
        if not case or case.guild_id != interaction.guild_id or case.kind != "ban":
            await interaction.response.send_message("Бан не найден.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"Дело #{case.id:05d}: {case.user_id}\nМодератор: {case.moderator_id}\nПричина: {case.reason}\n"
            f"Статус: {case.status}\nСрок до: {case.expires_at or 'per'}\nЛС: {case.dm_status}", ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none())

    @m.command(name="history", description="История наказаний участника")
    async def history(self, interaction: discord.Interaction, user: discord.User):
        if not await self.guard(interaction):
            return
        async with self.bot.database.sessions() as session:
            rows = (await session.scalars(select(ModerationCase).where(
                ModerationCase.guild_id == interaction.guild_id, ModerationCase.user_id == user.id
            ).order_by(ModerationCase.id.desc()).limit(25))).all()
        lines = [f"#{c.id} {c.kind}: {c.status} — {discord.utils.escape_mentions(c.reason[:75])}" for c in rows]
        await interaction.response.send_message("\n".join(lines)[:1800] or "История пуста.", ephemeral=True,
                                                allowed_mentions=discord.AllowedMentions.none())

    async def execute_clear(self, interaction: discord.Interaction, *, user: discord.User,
                            channel: discord.TextChannel, count: int, cutoff: dt.datetime | None,
                            reason: str) -> str:
        removed, scanned, failed = 0, 0, 0
        async for message in channel.history(limit=2000, after=cutoff):
            scanned += 1
            if message.author.id != user.id:
                continue
            try:
                await message.delete(reason=f"[NSB] {interaction.user.id}: {reason[:250]}")
                removed += 1
            except discord.HTTPException:
                failed += 1
            if removed + failed >= count:
                break
        await log_event(self.bot, channel_setting="audit_channel_id", title="Очистка сообщений",
            details=f"Модератор: {interaction.user.id}; цель: {user.id}; канал: {channel.id}; "
                    f"удалено: {removed}; ошибок: {failed}; проверено: {scanned}; причина: {reason[:300]}",
            actor_id=interaction.user.id, action="moderation.clear", target_id=user.id)
        return (f"Удалено сообщений: {removed}. Ошибок: {failed}. Проверено: {scanned}."
                + (" Достигнут лимит поиска 2000 сообщений." if scanned == 2000 else ""))

    @m.command(name="clear", description="Удалить сообщения участника по количеству или времени")
    async def clear(self, interaction: discord.Interaction, user: discord.User, count_or_period: str,
                    channel: discord.TextChannel | None = None, reason: str | None = None):
        if not await self.guard(interaction):
            return
        selected_channel = channel or interaction.channel
        if not isinstance(selected_channel, discord.TextChannel) or selected_channel.guild.id != interaction.guild_id:
            await interaction.response.send_message("Укажите текстовый канал NewSquad.", ephemeral=True)
            return
        if count_or_period.isascii() and count_or_period.isdecimal():
            count = int(count_or_period)
            if not 1 <= count <= 100:
                await interaction.response.send_message("За одну операцию удаляем от 1 до 100 сообщений.", ephemeral=True)
                return
            cutoff = None
        else:
            try:
                seconds = parse_duration(count_or_period, allow_permanent=False, max_seconds=31*86400)
            except ValueError as exc:
                await interaction.response.send_message(str(exc), ephemeral=True)
                return
            count = 100
            cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=seconds)
        reason = (reason or "Причина не указана").strip()[:400]
        if count > 30 or cutoff is not None:
            await interaction.response.send_message(
                f"Подтвердите удаление сообщений пользователя <@{user.id}> "
                f"в канале {selected_channel.mention} (диапазон: {count_or_period}; максимум 100 удалений).",
                view=ConfirmClear(self, interaction.user.id, user, selected_channel, count, cutoff, reason),
                ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await self.execute_clear(interaction, user=user, channel=selected_channel,
                                          count=count, cutoff=cutoff, reason=reason)
        await interaction.followup.send(result, ephemeral=True)


class ConfirmClear(LoggedView):
    def __init__(self, cog: Moderation, moderator_id: int, user: discord.User,
                 channel: discord.TextChannel, count: int, cutoff: dt.datetime | None, reason: str):
        super().__init__(timeout=90)
        self.cog, self.moderator_id, self.user, self.channel = cog, moderator_id, user, channel
        self.count, self.cutoff, self.reason = count, cutoff, reason

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.moderator_id or not await self.cog.can_moderate(interaction):
            await interaction.response.send_message("Подтвердить может только модератор, запустивший команду.",
                                                    ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Удалить", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="Удаление выполняется...", view=self)
        result = await self.cog.execute_clear(interaction, user=self.user, channel=self.channel,
                                               count=self.count, cutoff=self.cutoff, reason=self.reason)
        await interaction.followup.send(result, ephemeral=True)
        self.stop()

    @discord.ui.button(label="Отмена", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="Очистка отменена.", view=self)
        self.stop()


async def setup(bot):
    await bot.add_cog(Moderation(bot), guild=discord.Object(id=bot.config.guild_id))
