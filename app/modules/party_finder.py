"""Party Finder: объявления, постоянные кнопки и временные игровые группы."""
from __future__ import annotations

import asyncio
import logging
import time

import discord
from discord import app_commands
from discord.ext import commands

from app.core.durations import parse_duration
from app.core.permissions import can_manage_settings
from app.services import party_finder as store
from app.services.role_rules import is_approved
from app.ui.theme import LoggedView, card, notice

log = logging.getLogger(__name__)


def safe_text(text: str, max_length: int) -> str:
    value = " ".join(text.strip().split())
    if not value or len(value) > max_length or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f"Укажите текст от 1 до {max_length} символов.")
    return value


def party_embed(listing) -> discord.Embed:
    states = {"open": "Идёт набор", "full": "Мест нет", "closed": "Закрыта", "expired": "Срок истёк"}
    e = card(f"NewSquad / Ищем команду #{listing.id:04d}",
        f"**Игра:** {discord.utils.escape_mentions(listing.game)}\n"
        f"**Автор:** <@{listing.owner_id}>\n"
        f"**Описание:** {discord.utils.escape_mentions(listing.description)}")
    e.add_field(name="Участники", value=f"{listing.member_count}/{listing.capacity}", inline=True)
    e.add_field(name="Статус", value=states.get(listing.status, listing.status), inline=True)
    e.add_field(name="Действует до", value=f"<t:{listing.expires_at}:R>", inline=True)
    if listing.starts_at:
        e.add_field(name="Начало", value=f"<t:{listing.starts_at}:f>", inline=True)
    if listing.voice_channel_id:
        e.add_field(name="Голосовой канал", value=f"<#{listing.voice_channel_id}>", inline=False)
    elif listing.voice_requested:
        e.add_field(name="Голосовой канал", value="Пока недоступен", inline=False)
    e.set_footer(text=f"NewSquad • Группа #{listing.id:04d}")
    return e


class PartyButton(discord.ui.Button):
    def __init__(self, cog: "PartyFinder", party_id: int, action: str, *, disabled: bool = False):
        labels = {"join": "Присоединиться", "leave": "Покинуть",
                  "members": "Участники", "close": "Закрыть"}
        styles = {"join": discord.ButtonStyle.primary, "leave": discord.ButtonStyle.secondary,
                  "members": discord.ButtonStyle.primary, "close": discord.ButtonStyle.danger}
        super().__init__(label=labels[action], style=styles[action],
                         custom_id=f"newsqad:party:{party_id}:{action}", disabled=disabled)
        self.cog, self.party_id, self.action = cog, party_id, action

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.cog.perform(interaction, self.party_id, self.action)


class PartyView(LoggedView):
    def __init__(self, cog: "PartyFinder", party_id: int, *, full: bool = False):
        super().__init__(timeout=None)
        for action in ("join", "leave", "members", "close"):
            self.add_item(PartyButton(cog, party_id, action, disabled=full and action == "join"))


class PartyFinder(commands.Cog):
    party = app_commands.Group(name="party", description="Поиск компании для игр NewSquad")

    def __init__(self, bot):
        self.bot = bot
        self._worker: asyncio.Task | None = None
        self._initialized = False

    async def cog_load(self) -> None:
        for row in await store.list_active(self.bot.database, self.bot.config.guild_id, limit=500):
            if row.message_id:
                self.bot.add_view(PartyView(self, row.id, full=row.status == "full"), message_id=row.message_id)

    async def cog_unload(self) -> None:
        if self._worker:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass

    async def permitted(self, member: discord.Member) -> bool:
        if not isinstance(member, discord.Member) or member.bot or member.guild.id != self.bot.config.guild_id:
            return False
        candidate_role_id = await self.bot.database.get_setting(member.guild.id, "candidate_role_id")
        return is_approved(member, candidate_role_id)

    async def enabled(self) -> bool:
        return bool(await self.bot.database.get_setting(self.bot.config.guild_id, "party_enabled", True))

    async def _publish(self, row) -> bool:
        if row.message_id:
            return True
        guild = self.bot.get_guild(row.guild_id)
        channel = guild.get_channel(row.channel_id) if guild and row.channel_id else None
        if not isinstance(channel, discord.TextChannel):
            log.error("Канал Party Finder не найден: party_id=%s channel_id=%s", row.id, row.channel_id)
            return False
        msg = await channel.send(embed=party_embed(row),
                                 view=PartyView(self, row.id, full=row.status == "full"),
                                 allowed_mentions=discord.AllowedMentions.none())
        await store.set_message(self.bot.database, row.guild_id, row.id, msg.id)
        self.bot.add_view(PartyView(self, row.id, full=row.status == "full"), message_id=msg.id)
        return True

    async def refresh(self, party_id: int) -> None:
        row = await store.get(self.bot.database, self.bot.config.guild_id, party_id)
        if not row or not row.message_id:
            return
        guild = self.bot.get_guild(row.guild_id)
        channel = guild.get_channel(row.channel_id) if guild else None
        if not isinstance(channel, discord.TextChannel):
            log.warning("Публикация группы #%s недоступна: канал удалён", party_id)
            return
        try:
            msg = await channel.fetch_message(row.message_id)
            await msg.edit(embed=party_embed(row),
                view=PartyView(self, row.id, full=row.status == "full") if row.status in store.ACTIVE else None,
                allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            log.exception("Не удалось обновить карточку Party Finder party_id=%s", party_id)

    async def _create_voice(self, row, member: discord.Member) -> bool:
        voice = self.bot.get_cog("Voice")
        if voice is None or not await self.bot.database.get_setting(row.guild_id, "voice_enabled", True):
            return False
        category_id = await self.bot.database.get_setting(row.guild_id, "voice_category_channel_id")
        category = member.guild.get_channel(category_id) if category_id else None
        if not isinstance(category, discord.CategoryChannel):
            return False
        try:
            overwrites = await voice._access_overwrites(member.guild, member)
            channel = await member.guild.create_voice_channel(
                f"Команда #{row.id} • {row.game}"[:100], category=category,
                overwrites=overwrites, user_limit=row.capacity,
                reason=f"[NSB-PARTY-{row.id}] Голосовая комната группы")
        except (discord.HTTPException, ValueError):
            log.exception("Не удалось создать голосовую группу party_id=%s", row.id)
            return False
        try:
            await store.set_voice(self.bot.database, row.guild_id, row.id, channel.id)
        except Exception:
            log.exception("Не удалось сохранить голосовой канал party_id=%s", row.id)
            try:
                await channel.delete(reason="Откат создания комнаты Party Finder")
            except discord.HTTPException:
                log.exception("Не удалось откатить голосовую комнату party_id=%s", row.id)
            return False
        return True

    async def _cleanup_voice(self, row) -> None:
        if not row.voice_channel_id:
            return
        guild = self.bot.get_guild(row.guild_id)
        channel = guild.get_channel(row.voice_channel_id) if guild else None
        if isinstance(channel, discord.VoiceChannel):
            try:
                await channel.delete(reason=f"[NSB-PARTY-{row.id}] Группа завершена")
            except discord.NotFound:
                pass
            except discord.HTTPException:
                log.exception("Не удалось удалить комнату группы party_id=%s", row.id)

    async def _tick(self) -> None:
        while True:
            try:
                await self.sweep()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Ошибка фонового обслуживания Party Finder")
            await asyncio.sleep(60)

    async def sweep(self) -> None:
        gid = self.bot.config.guild_id
        expired = await store.expire_due(self.bot.database, gid)
        for party_id in expired:
            row = await store.get(self.bot.database, gid, party_id)
            if row:
                await self.refresh(party_id)
                await self._cleanup_voice(row)

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        await self.sweep()
        rows = await store.list_active(self.bot.database, self.bot.config.guild_id, limit=500)
        for row in rows:
            if not row.message_id:
                try:
                    await self._publish(row)
                except discord.HTTPException:
                    log.exception("Повторная публикация группы party_id=%s", row.id)
        self._worker = asyncio.create_task(self._tick(), name="newsqad-party-expiry")

    async def perform(self, interaction: discord.Interaction, party_id: int, action: str) -> None:
        if interaction.guild_id != self.bot.config.guild_id or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(embed=notice("Недоступно на этом сервере.", kind="warning"),
                                                    ephemeral=True)
            return
        if action != "close" and (not await self.enabled() or not await self.permitted(interaction.user)):
            await interaction.response.send_message(embed=notice("Только принятым участникам NewSquad.", kind="warning"),
                                                    ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            row = await store.get(self.bot.database, interaction.guild_id, party_id)
            if row is None:
                raise ValueError("Группа не найдена.")
            if action == "join":
                row = await store.join(self.bot.database, interaction.guild_id, party_id, interaction.user.id)
                msg = "Вы присоединились к группе."
            elif action == "leave":
                row = await store.leave(self.bot.database, interaction.guild_id, party_id, interaction.user.id)
                msg = "Вы вышли из группы."
            elif action == "close":
                admin = can_manage_settings(interaction, self.bot.config.owner_ids)
                row = await store.close(self.bot.database, interaction.guild_id, party_id,
                                        interaction.user.id, admin=admin)
                await self._cleanup_voice(row)
                msg = "Объявление закрыто."
            elif action == "members":
                ids = await store.members(self.bot.database, party_id)
                msg = "Состав группы:\n" + "\n".join(f"• <@{uid}>" for uid in ids)
            else:
                raise ValueError("Неизвестное действие.")
        except ValueError as exc:
            await interaction.followup.send(embed=notice(str(exc), kind="warning"), ephemeral=True)
            return
        if action != "members":
            await self.refresh(party_id)
        await interaction.followup.send(embed=notice(msg, kind="success"), ephemeral=True,
                                        allowed_mentions=discord.AllowedMentions.none())

    @party.command(name="create", description="Опубликовать поиск тиммейтов")
    async def create(self, interaction: discord.Interaction, game: str, description: str,
                     capacity: app_commands.Range[int, 2, 25] = 5, expires_in: str = "6h",
                     start_in: str | None = None, voice: bool = False) -> None:
        if not await self.enabled() or not await self.permitted(interaction.user):
            await interaction.response.send_message(embed=notice("Поиск доступен только участникам.", kind="warning"),
                                                    ephemeral=True)
            return
        channel_id = await self.bot.database.get_setting(interaction.guild_id, "party_channel_id")
        channel = interaction.guild.get_channel(channel_id) if channel_id else None
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(embed=notice("Администрация ещё не настроила канал поиска.",
                                                      kind="warning"), ephemeral=True)
            return
        if voice and (self.bot.get_cog("Voice") is None or not
                      await self.bot.database.get_setting(interaction.guild_id, "voice_enabled", True)):
            await interaction.response.send_message(embed=notice("Модуль голосовых комнат отключён.",
                                                      kind="warning"), ephemeral=True)
            return
        try:
            game = safe_text(game, 80)
            description = safe_text(description, 500)
            seconds = parse_duration(expires_in, allow_permanent=False, max_seconds=7 * 86400)
            start_seconds = parse_duration(start_in, allow_permanent=False, max_seconds=7 * 86400) if start_in else None
            now = int(time.time())
            if start_seconds is not None and start_seconds >= seconds:
                raise ValueError("Начало должно быть раньше окончания объявления.")
        except ValueError as exc:
            await interaction.response.send_message(embed=notice(str(exc), kind="warning"), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            row = await store.create(self.bot.database, guild_id=interaction.guild_id,
                owner_id=interaction.user.id, game=game, description=description, capacity=capacity,
                expires_at=now + seconds, starts_at=now + start_seconds if start_seconds else None,
                channel_id=channel.id, voice_requested=voice)
            voice_ok = await self._create_voice(row, interaction.user) if voice else False
            row = await store.get(self.bot.database, row.guild_id, row.id)
            published = await self._publish(row)
            msg = f"Поиск #{row.id:04d} создан." if published else (
                f"Поиск #{row.id:04d} сохранён, но публикация пока не удалась.")
            if voice and not voice_ok:
                msg += " Голосовую комнату создать не удалось; поиск продолжит работать без неё."
            await interaction.followup.send(embed=notice(msg, kind="success" if published else "warning"),
                                            ephemeral=True)
        except ValueError as exc:
            await interaction.followup.send(embed=notice(str(exc), kind="warning"), ephemeral=True)
        except discord.HTTPException:
            log.exception("Не удалось опубликовать поиск команды")
            await interaction.followup.send(embed=notice("Объявление сохранено, повторная публикация после запуска бота.",
                                                kind="warning"), ephemeral=True)

    @party.command(name="list", description="Посмотреть открытые группы")
    async def list_command(self, interaction: discord.Interaction, game: str | None = None) -> None:
        if not await self.enabled() or not await self.permitted(interaction.user):
            await interaction.response.send_message(embed=notice("Только для участников.", kind="warning"),
                                                    ephemeral=True)
            return
        rows = await store.list_active(self.bot.database, interaction.guild_id, game=game)
        lines = [f"**#{r.id:04d}** · {discord.utils.escape_mentions(r.game)} · "
                 f"{r.member_count}/{r.capacity} · <t:{r.expires_at}:R>" for r in rows]
        await interaction.response.send_message(embed=card("NewSquad / Поиск команды",
            "\n".join(lines) or "Открытых объявлений пока нет."), ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none())

    @party.command(name="join", description="Присоединиться к группе")
    async def join_command(self, interaction: discord.Interaction, number: int) -> None:
        await self.perform(interaction, number, "join")

    @party.command(name="leave", description="Покинуть группу")
    async def leave_command(self, interaction: discord.Interaction, number: int) -> None:
        await self.perform(interaction, number, "leave")

    @party.command(name="close", description="Закрыть свою группу")
    async def close_command(self, interaction: discord.Interaction, number: int) -> None:
        await self.perform(interaction, number, "close")

    @party.command(name="settings", description="Настройки поиска команды")
    async def settings(self, interaction: discord.Interaction) -> None:
        if not can_manage_settings(interaction, self.bot.config.owner_ids):
            await interaction.response.send_message(embed=notice("Только администрация.", kind="warning"),
                                                    ephemeral=True)
            return
        channel = await self.bot.database.get_setting(interaction.guild_id, "party_channel_id")
        enabled = await self.enabled()
        await interaction.response.send_message(embed=card("NewSquad / Party Finder",
            f"**Включён:** {'да' if enabled else 'нет'}\n"
            f"**Канал поиска:** {f'<#{channel}>' if channel else 'не настроен'}\n"
            "**Лимит:** одно активное объявление от автора.\n"
            "**Время жизни:** от 1 минуты до 7 дней.\n"
            "Используйте /party configure для настройки."), ephemeral=True)

    @party.command(name="configure", description="Указать канал поиска и включить модуль")
    async def configure(self, interaction: discord.Interaction, channel: discord.TextChannel,
                        enabled: bool = True) -> None:
        if not can_manage_settings(interaction, self.bot.config.owner_ids):
            await interaction.response.send_message(embed=notice("Только администрация.", kind="warning"),
                                                    ephemeral=True)
            return
        if channel.guild.id != self.bot.config.guild_id:
            await interaction.response.send_message(embed=notice("Нужен канал NewSquad.", kind="warning"),
                                                    ephemeral=True)
            return
        for key, value in (("party_channel_id", channel.id), ("party_enabled", enabled)):
            await self.bot.database.set_setting(interaction.guild_id, key, value, actor_id=interaction.user.id)
        await interaction.response.send_message(embed=notice("Канал и настройки сохранены.", kind="success"),
                                                ephemeral=True)


async def setup(bot) -> None:
    await bot.add_cog(PartyFinder(bot), guild=discord.Object(id=bot.config.guild_id))
