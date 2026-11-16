"""Голосовые комнаты NewSquad: лобби, приватность, владельцы и восстановление."""
from __future__ import annotations

import asyncio
import logging
import re

import discord
from discord import app_commands
from discord.ext import commands, tasks

from app.core.permissions import can_manage_settings
from app.services import voice as store
from app.services.membership_access import approved_member
from app.ui.theme import LoggedModal, LoggedView, card, notice

log = logging.getLogger(__name__)


def room_name(raw: str) -> str:
    """Не разрешаем управляющие символы и упоминания в имени создаваемого канала."""
    name = re.sub(r"[\r\n\t\x00-\x1f\x7f]", " ", raw)
    name = re.sub(r"\s+", " ", name).strip()
    if not 2 <= len(name) <= 90 or "@everyone" in name.lower() or "@here" in name.lower():
        raise ValueError("Название должно содержать 2–90 символов без массовых упоминаний.")
    return name


def describe_room(room, channel: discord.VoiceChannel | None = None) -> discord.Embed:
    count = len(channel.members) if channel else 0
    embed = card("NewSquad  /  Голосовая комната",
        f"**{discord.utils.escape_markdown(room.name)}**\n"
        f"Канал: <#{room.channel_id}>\nВладелец: <@{room.owner_id}>\n"
        f"Участники: **{count}** · Лимит: **{room.user_limit or 'без лимита'}**\n"
        f"Вход: **{'по приглашению' if room.locked else 'свободный'}** · "
        f"Видимость: **{'скрыта' if room.hidden else 'открыта'}**\n\n"
        "Кнопки доступны владельцу комнаты. Настройка доступа: `/voice allow` и `/voice deny`.")
    embed.set_footer(text="NewSquad • Комнаты удаляются автоматически, когда пустеют")
    return embed


class RenameRoomModal(LoggedModal, title="Имя голосовой комнаты"):
    name = discord.ui.TextInput(label="Новое название", min_length=2, max_length=90)

    def __init__(self, cog: "Voice", channel_id: int):
        super().__init__(timeout=300)
        self.cog, self.channel_id = cog, channel_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await self.cog.rename_room(interaction, self.channel_id, self.name.value)
        await interaction.followup.send(embed=notice(result, kind="success" if result.startswith("Готово") else "warning"),
                                        ephemeral=True)


class LimitRoomModal(LoggedModal, title="Лимит голосовой комнаты"):
    amount = discord.ui.TextInput(label="Мест (0 — без лимита)", min_length=1, max_length=2, placeholder="0")

    def __init__(self, cog: "Voice", channel_id: int):
        super().__init__(timeout=300)
        self.cog, self.channel_id = cog, channel_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            amount = int(self.amount.value)
        except ValueError:
            result = "Укажите число от 0 до 99."
        else:
            result = await self.cog.limit_room(interaction, self.channel_id, amount)
        await interaction.followup.send(embed=notice(result, kind="success" if result.startswith("Готово") else "warning"),
                                        ephemeral=True)


class CloseRoomConfirm(LoggedView):
    def __init__(self, cog: "Voice", channel_id: int):
        super().__init__(timeout=90)
        self.cog, self.channel_id = cog, channel_id

    @discord.ui.button(label="Удалить комнату", style=discord.ButtonStyle.danger)
    async def accept(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await self.cog.delete_room(interaction, self.channel_id)
        await interaction.followup.send(embed=notice(result, kind="success" if result.startswith("Готово") else "warning"),
                                        ephemeral=True)

    @discord.ui.button(label="Отмена", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(embed=notice("Удаление отменено."), view=None)


class RoomControls(LoggedView):
    def __init__(self, cog: "Voice", channel_id: int):
        super().__init__(timeout=600)
        self.cog, self.channel_id = cog, channel_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not await super().interaction_check(interaction):
            return False
        room = await store.by_channel(self.cog.bot.database, interaction.guild_id or 0, self.channel_id)
        admin = can_manage_settings(interaction, self.cog.bot.config.owner_ids)
        owner = (room is not None and room.owner_id == interaction.user.id
                 and isinstance(interaction.user, discord.Member)
                 and await self.cog._approved(interaction.user))
        if room is None or not (owner or admin):
            await interaction.response.send_message(embed=notice("Эти кнопки доступны только владельцу комнаты.",
                kind="warning"), ephemeral=True)
            return False
        return True

    async def _toggle(self, interaction: discord.Interaction, field: str) -> None:
        await interaction.response.defer()
        room = await store.by_channel(self.cog.bot.database, interaction.guild_id, self.channel_id)
        if room is None:
            await interaction.followup.send(embed=notice("Комната уже удалена.", kind="warning"), ephemeral=True)
            return
        result = await self.cog.visibility(interaction, self.channel_id, field, not getattr(room, field))
        if not result.startswith("Готово"):
            await interaction.followup.send(embed=notice(result, kind="warning"), ephemeral=True)
            return
        room = await store.by_channel(self.cog.bot.database, interaction.guild_id, self.channel_id)
        channel = interaction.guild.get_channel(self.channel_id)
        await interaction.edit_original_response(embed=describe_room(room, channel), view=RoomControls(self.cog, self.channel_id))

    @discord.ui.button(label="Закрыть / Открыть вход", emoji="🔒", style=discord.ButtonStyle.primary, row=0)
    async def lock(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._toggle(interaction, "locked")

    @discord.ui.button(label="Скрыть / Показать", emoji="👁️", style=discord.ButtonStyle.secondary, row=0)
    async def hide(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._toggle(interaction, "hidden")

    @discord.ui.button(label="Название", emoji="✏️", style=discord.ButtonStyle.secondary, row=0)
    async def rename(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(RenameRoomModal(self.cog, self.channel_id))

    @discord.ui.button(label="Лимит", emoji="👥", style=discord.ButtonStyle.secondary, row=1)
    async def limit(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(LimitRoomModal(self.cog, self.channel_id))

    @discord.ui.button(label="Удалить", emoji="🗑️", style=discord.ButtonStyle.danger, row=1)
    async def close(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_message(embed=notice("Удаление комнаты необратимо. В комнате не должно быть "
            "других участников.", kind="warning"), view=CloseRoomConfirm(self.cog, self.channel_id), ephemeral=True)

    @discord.ui.button(label="Обновить", emoji="🔄", style=discord.ButtonStyle.secondary, row=1)
    async def refresh(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.defer()
        room = await store.by_channel(self.cog.bot.database, interaction.guild_id, self.channel_id)
        if room is None:
            await interaction.edit_original_response(embed=notice("Комната удалена.", kind="warning"), view=None)
            return
        await interaction.edit_original_response(
            embed=describe_room(room, interaction.guild.get_channel(self.channel_id)), view=self)


class Voice(commands.Cog):
    voice_commands = app_commands.Group(name="voice", description="Управление временными голосовыми комнатами")

    def __init__(self, bot):
        self.bot = bot
        self._creation_locks: dict[int, asyncio.Lock] = {}
        self._creation_guard = asyncio.Lock()
        self._handoff_locks: dict[int, asyncio.Lock] = {}
        self._reconcile_lock = asyncio.Lock()
        self._room_locks: dict[int, asyncio.Lock] = {}

    async def cog_load(self) -> None:
        self.sweep.start()

    async def cog_unload(self) -> None:
        self.sweep.cancel()

    async def _settings(self) -> dict:
        db, gid = self.bot.database, self.bot.config.guild_id
        return {key: await db.get_setting(gid, f"voice_{key}", default) for key, default in (
            ("enabled", True), ("lobby_channel_id", None), ("category_channel_id", None),
            ("default_limit", 0), ("max_rooms", 30))}

    def _privileged(self, member: discord.Member) -> bool:
        """Администрация не обязана подавать анкету для создания комнаты."""
        if member.bot or member.guild.id != self.bot.config.guild_id:
            return False
        permissions = getattr(member, "guild_permissions", None)
        return (member.id == member.guild.owner_id or
                member.id in self.bot.config.owner_ids or
                bool(permissions and permissions.administrator))

    async def _approved(self, member: discord.Member) -> bool:
        if member.bot or member.guild.id != self.bot.config.guild_id:
            return False
        if self._privileged(member):
            return True
        role_id = await self.bot.database.get_setting(member.guild.id, "member_role_id")
        return await approved_member(self.bot.database, member, role_id)

    def _creation_problems(self, guild: discord.Guild, lobby: discord.VoiceChannel,
                           category: discord.CategoryChannel) -> list[str]:
        """Называем недостающие разрешения до Discord API-запроса."""
        me = self._bot_member(guild)
        if me is None:
            return ["Бот не найден среди участников сервера."]
        issues = []
        if not me.guild_permissions.manage_channels:
            issues.append("У бота нет серверного разрешения «Управлять каналами» (Manage Channels).")
        if not me.guild_permissions.move_members:
            issues.append("У бота нет серверного разрешения «Перемещать участников» (Move Members).")
        if not lobby.permissions_for(me).move_members:
            issues.append("У бота нет «Перемещать участников» в канале создания.")
        if not category.permissions_for(me).manage_channels:
            issues.append("У бота нет «Управлять каналами» в категории временных комнат.")
        return issues

    def _bot_member(self, guild: discord.Guild) -> discord.Member | None:
        return guild.me or (guild.get_member(self.bot.user.id) if self.bot.user else None)

    @staticmethod
    def _permissions_ok(me: discord.Member | None) -> bool:
        return bool(me and me.guild_permissions.manage_channels and me.guild_permissions.move_members)

    async def _authorized(self, interaction: discord.Interaction, channel_id: int,
                          *, admin_ok: bool = True):
        if interaction.guild_id != self.bot.config.guild_id or not isinstance(interaction.user, discord.Member):
            return None
        room = await store.by_channel(self.bot.database, interaction.guild_id, channel_id)
        admin = admin_ok and can_manage_settings(interaction, self.bot.config.owner_ids)
        if room and (admin or (room.owner_id == interaction.user.id and await self._approved(interaction.user))):
            return room
        return None

    async def _own_room(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None or guild.id != self.bot.config.guild_id or not isinstance(interaction.user, discord.Member):
            return None
        admin = can_manage_settings(interaction, self.bot.config.owner_ids)
        if not admin and not await self._approved(interaction.user):
            return None
        voice = interaction.user.voice
        if voice and voice.channel:
            room = await store.by_channel(self.bot.database, guild.id, voice.channel.id)
            if room and (room.owner_id == interaction.user.id or admin):
                return room
        return await store.by_owner(self.bot.database, guild.id, interaction.user.id)

    async def _access_overwrites(self, guild: discord.Guild, owner: discord.Member):
        member_role_id = await self.bot.database.get_setting(guild.id, "member_role_id")
        member_role = guild.get_role(member_role_id) if member_role_id else None
        me = self._bot_member(guild)
        if member_role is None and not self._privileged(owner):
            raise ValueError("Назначьте роль участника в /botsettings → Роли.")
        if not self._permissions_ok(me):
            raise ValueError("Для комнат нужны серверные права Manage Channels и Move Members у бота.")
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False, connect=False),
            owner: discord.PermissionOverwrite(view_channel=True, connect=True, speak=True),
            me: discord.PermissionOverwrite(view_channel=True, connect=True, move_members=True, manage_channels=True),
        }
        if member_role is not None:
            overwrites[member_role] = discord.PermissionOverwrite(view_channel=True, connect=True)
        return overwrites

    async def _create_for(self, member: discord.Member, lobby: discord.VoiceChannel) -> None:
        lock = self._creation_locks.setdefault(member.id, asyncio.Lock())
        async with lock, self._creation_guard:
            settings = await self._settings()
            if not settings["enabled"] or lobby.id != settings["lobby_channel_id"]:
                log.info("Создание комнаты отключено/другой генератор guild=%s lobby=%s configured=%s enabled=%s",
                         member.guild.id, lobby.id, settings["lobby_channel_id"], settings["enabled"])
                return
            if member.voice is None or member.voice.channel != lobby:
                log.info("Участник уже вышел из канала создания guild=%s user=%s",
                         member.guild.id, member.id)
                return
            if not await self._approved(member):
                log.warning("Создание комнаты запрещено: нет одобренной заявки или роли участника "
                            "guild=%s user=%s; для администрации разрешён обход Membership",
                            member.guild.id, member.id)
                return
            old = await store.by_owner(self.bot.database, member.guild.id, member.id)
            if old:
                channel = member.guild.get_channel(old.channel_id)
                if isinstance(channel, discord.VoiceChannel):
                    try:
                        await member.move_to(channel, reason="NewSquad: возвращение в свою комнату")
                    except discord.HTTPException:
                        log.exception("Не удалось вернуть участника в комнату owner=%s room=%s", member.id, channel.id)
                    return
                await store.remove(self.bot.database, member.guild.id, old.channel_id)
            parent = member.guild.get_channel(settings["category_channel_id"])
            if not isinstance(parent, discord.CategoryChannel):
                log.warning("Категория комнат отсутствует/неверного типа guild=%s category_id=%s",
                            member.guild.id, settings["category_channel_id"])
                return
            issues = self._creation_problems(member.guild, lobby, parent)
            if issues:
                log.warning("Создание временной комнаты запрещено guild=%s user=%s: %s",
                            member.guild.id, member.id, " | ".join(issues))
                return
            rooms = await store.all_rooms(self.bot.database, member.guild.id)
            if len(rooms) >= int(settings["max_rooms"]):
                log.warning("Достигнут предел комнат guild=%s limit=%s", member.guild.id, settings["max_rooms"])
                return
            try:
                overwrites = await self._access_overwrites(member.guild, member)
                name = room_name(f"Комната {member.display_name[:65]}")
                limit = int(settings["default_limit"])
                channel = await member.guild.create_voice_channel(name, category=parent,
                    overwrites=overwrites, user_limit=limit,
                    reason=f"[NSB-VOICE] Новая комната участника {member.id}")
            except (discord.HTTPException, ValueError):
                log.exception("Не удалось создать комнату user=%s guild=%s", member.id, member.guild.id)
                return
            try:
                await store.register(self.bot.database, guild_id=member.guild.id, channel_id=channel.id,
                                     owner_id=member.id, lobby_id=lobby.id, name=name, user_limit=limit)
            except Exception:
                log.exception("Не удалось сохранить созданную комнату channel=%s", channel.id)
                try:
                    await channel.delete(reason="Откат регистрации голосовой комнаты")
                except discord.HTTPException:
                    log.exception("Не удалось удалить незарегистрированную комнату channel=%s", channel.id)
                return
            if member.voice is None or member.voice.channel != lobby:
                await self._cleanup_if_empty(member.guild, channel.id)
                return
            try:
                await member.move_to(channel, reason="NewSquad: создание временной комнаты")
            except discord.HTTPException:
                log.exception("Создана комната, но перенос пользователя не удался user=%s channel=%s",
                              member.id, channel.id)
                await self._cleanup_if_empty(member.guild, channel.id)
                return
            log.info("Создана временная комната guild=%s channel=%s owner=%s", member.guild.id, channel.id, member.id)

    async def _cleanup_if_empty(self, guild: discord.Guild, channel_id: int) -> None:
        lock = self._room_locks.setdefault(channel_id, asyncio.Lock())
        async with lock:
            room = await store.by_channel(self.bot.database, guild.id, channel_id)
            if room is None:
                return
            channel = guild.get_channel(channel_id)
            if channel is None:
                await store.remove(self.bot.database, guild.id, channel_id)
                return
            if not isinstance(channel, discord.VoiceChannel) or channel.members:
                return
            try:
                await channel.delete(reason="[NSB-VOICE] Пустая временная комната")
            except (discord.Forbidden, discord.HTTPException):
                log.exception("Ошибка удаления пустой комнаты guild=%s channel=%s", guild.id, channel_id)
                return
            await store.remove(self.bot.database, guild.id, channel_id)
            log.info("Удалена пустая комната guild=%s channel=%s", guild.id, channel_id)

    async def _handoff(self, guild: discord.Guild, channel_id: int) -> None:
        lock = self._handoff_locks.setdefault(channel_id, asyncio.Lock())
        async with lock:
            await self._handoff_locked(guild, channel_id)

    async def _handoff_locked(self, guild: discord.Guild, channel_id: int) -> None:
        room = await store.by_channel(self.bot.database, guild.id, channel_id)
        channel = guild.get_channel(channel_id)
        if room is None:
            return
        if channel is None:
            await store.remove(self.bot.database, guild.id, channel_id)
            log.warning("Удалена запись отсутствующей комнаты guild=%s channel=%s", guild.id, channel_id)
            return
        if not isinstance(channel, discord.VoiceChannel):
            log.error("Сохранённый канал комнаты имеет неверный тип guild=%s channel=%s", guild.id, channel_id)
            return
        if not channel.members:
            await self._cleanup_if_empty(guild, channel_id)
            return
        if any(m.id == room.owner_id for m in channel.members):
            return
        for member in channel.members:
            if await self._approved(member) and await store.by_owner(self.bot.database, guild.id, member.id) is None:
                try:
                    await self._transfer_owner(guild, room, member)
                except (ValueError, discord.HTTPException):
                    log.exception("Не удалось передать комнату channel=%s user=%s", channel_id, member.id)
                return
        log.info("Комната %s осталась без владельца; доступна команда /voice claim", channel_id)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState,
                                    after: discord.VoiceState) -> None:
        if member.guild.id != self.bot.config.guild_id or member.bot or before.channel == after.channel:
            return
        log.info("Голосовое подключение guild=%s user=%s from=%s to=%s",
                 member.guild.id, member.id,
                 before.channel.id if before.channel else None,
                 after.channel.id if after.channel else None)
        if before.channel is not None:
            room = await store.by_channel(self.bot.database, member.guild.id, before.channel.id)
            if room:
                await asyncio.sleep(0.5)
                await self._handoff(member.guild, before.channel.id)
        if after.channel is not None:
            settings = await self._settings()
            if settings["enabled"] and after.channel.id == settings["lobby_channel_id"]:
                await self._create_for(member, after.channel)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        if channel.guild.id == self.bot.config.guild_id and isinstance(channel, discord.VoiceChannel):
            await store.remove(self.bot.database, channel.guild.id, channel.id)

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        await self._reconcile()
        # Discord не присылает повторно on_voice_state_update для тех,
        # кто уже находился в генераторе до запуска контейнера.
        settings = await self._settings()
        guild = self.bot.get_guild(self.bot.config.guild_id)
        lobby = guild.get_channel(settings["lobby_channel_id"]) if guild else None
        if settings["enabled"] and isinstance(lobby, discord.VoiceChannel):
            for member in tuple(lobby.members):
                if member.bot:
                    continue
                try:
                    await self._create_for(member, lobby)
                except Exception:
                    log.exception("Не удалось восстановить голосовой вход guild=%s user=%s",
                                  member.guild.id, member.id)

    async def _reconcile(self) -> None:
        async with self._reconcile_lock:
            guild = self.bot.get_guild(self.bot.config.guild_id)
            if guild is None:
                return
            for room in await store.all_rooms(self.bot.database, guild.id):
                try:
                    await self._handoff(guild, room.channel_id)
                except Exception:
                    log.exception("Не удалось восстановить комнату guild=%s channel=%s", guild.id, room.channel_id)

    @tasks.loop(minutes=2)
    async def sweep(self) -> None:
        await self._reconcile()

    @sweep.before_loop
    async def before_sweep(self) -> None:
        await self.bot.wait_until_ready()

    async def _transfer_owner(self, guild: discord.Guild, room, new: discord.Member) -> None:
        if new.id == room.owner_id:
            return
        if await store.by_owner(self.bot.database, guild.id, new.id):
            raise ValueError("У этого участника уже есть голосовая комната.")
        channel = guild.get_channel(room.channel_id)
        if not isinstance(channel, discord.VoiceChannel):
            raise ValueError("Голосовой канал больше не существует.")
        old_owner = guild.get_member(room.owner_id)
        old_overwrite = channel.overwrites_for(old_owner) if old_owner else None
        new_overwrite = channel.overwrites_for(new)
        try:
            await channel.set_permissions(new, view_channel=True, connect=True, speak=True,
                                          reason="NewSquad: передача владения комнатой")
            if old_owner:
                await channel.set_permissions(old_owner, overwrite=None,
                                              reason="NewSquad: прежний владелец")
            await store.transfer(self.bot.database, guild.id, room.channel_id, new.id)
        except Exception:
            log.exception("Ошибка переноса владельца channel=%s old=%s new=%s", room.channel_id, room.owner_id, new.id)
            try:
                await channel.set_permissions(new, overwrite=new_overwrite)
                if old_owner:
                    await channel.set_permissions(old_owner, overwrite=old_overwrite)
            except discord.HTTPException:
                log.exception("Не удалось откатить разрешения channel=%s", room.channel_id)
            raise
        log.info("Передана комната guild=%s channel=%s from=%s to=%s", guild.id, room.channel_id,
                 room.owner_id, new.id)

    async def rename_room(self, interaction: discord.Interaction, channel_id: int, value: str) -> str:
        room = await self._authorized(interaction, channel_id)
        if room is None:
            return "Нет доступа к этой комнате."
        try:
            name = room_name(value)
            channel = interaction.guild.get_channel(channel_id)
            if not isinstance(channel, discord.VoiceChannel):
                return "Канал уже удалён."
            await channel.edit(name=name, reason=f"NewSquad: имя комнаты изменил {interaction.user.id}")
            await store.rename(self.bot.database, interaction.guild_id, channel_id, name=name)
        except ValueError as exc:
            return str(exc)
        except discord.HTTPException:
            log.exception("Ошибка переименования комнаты channel=%s", channel_id)
            return "Discord не разрешил изменить название. Проверьте Manage Channels."
        return f"Готово. Новое название: **{discord.utils.escape_markdown(name)}**."

    async def limit_room(self, interaction: discord.Interaction, channel_id: int, amount: int) -> str:
        room = await self._authorized(interaction, channel_id)
        if room is None:
            return "Нет доступа к этой комнате."
        if not 0 <= amount <= 99:
            return "Лимит может быть от 0 (без ограничений) до 99 человек."
        channel = interaction.guild.get_channel(channel_id)
        if not isinstance(channel, discord.VoiceChannel):
            return "Канал уже удалён."
        try:
            await channel.edit(user_limit=amount, reason=f"NewSquad: лимит изменил {interaction.user.id}")
            await store.update_state(self.bot.database, interaction.guild_id, channel_id, user_limit=amount)
        except discord.HTTPException:
            log.exception("Не удалось изменить лимит channel=%s", channel_id)
            return "Discord не разрешил изменить лимит комнаты."
        return f"Готово. Лимит: **{amount or 'без ограничений'}**."

    async def visibility(self, interaction: discord.Interaction, channel_id: int, field: str, state: bool) -> str:
        room = await self._authorized(interaction, channel_id)
        if room is None:
            return "Нет доступа к этой комнате."
        if field not in {"locked", "hidden"}:
            return "Неизвестная настройка комнаты."
        channel = interaction.guild.get_channel(channel_id)
        role_id = await self.bot.database.get_setting(interaction.guild_id, "member_role_id")
        role = interaction.guild.get_role(role_id) if role_id else None
        if not isinstance(channel, discord.VoiceChannel) or role is None:
            return "Голосовой канал или роль участника не найдены."
        locked = state if field == "locked" else room.locked
        hidden = state if field == "hidden" else room.hidden
        try:
            await channel.set_permissions(role, view_channel=not hidden, connect=not locked,
                reason=f"NewSquad: настройки комнаты изменил {interaction.user.id}")
            await store.update_state(self.bot.database, interaction.guild_id, channel_id,
                                     locked=locked, hidden=hidden)
        except discord.HTTPException:
            log.exception("Не удалось изменить видимость комнаты channel=%s", channel_id)
            return "Discord отказал в изменении доступа."
        return "Готово. Параметры комнаты обновлены."

    async def access_room(self, interaction: discord.Interaction, channel_id: int,
                          member: discord.Member, mode: str | None) -> str:
        room = await self._authorized(interaction, channel_id)
        if room is None:
            return "Управлять доступом может только владелец или администратор."
        if member.bot or not await self._approved(member):
            return "Разрешения выдаются только одобренным участникам NewSquad."
        if member.id == room.owner_id:
            return "Нельзя изменить доступ владельца комнаты."
        if member.guild_permissions.administrator or member.id == member.guild.owner_id:
            return "Нельзя ограничивать доступ администрации сервера."
        channel = interaction.guild.get_channel(channel_id)
        if not isinstance(channel, discord.VoiceChannel):
            return "Канал больше не существует."
        try:
            if mode == "allow":
                await channel.set_permissions(member, view_channel=True, connect=True,
                    reason=f"NewSquad: персональный доступ от {interaction.user.id}")
            elif mode == "deny":
                await channel.set_permissions(member, view_channel=False, connect=False,
                    reason=f"NewSquad: доступ закрыт {interaction.user.id}")
                if member.voice and member.voice.channel == channel:
                    await member.move_to(None, reason="NewSquad: доступ в комнату закрыт")
            else:
                await channel.set_permissions(member, overwrite=None,
                    reason=f"NewSquad: снято исключение доступа {interaction.user.id}")
            if mode:
                await store.set_access(self.bot.database, room.id, member.id, mode)
            else:
                await store.clear_access(self.bot.database, room.id, member.id)
        except discord.HTTPException:
            log.exception("Ошибка изменения доступа room=%s user=%s mode=%s", channel_id, member.id, mode)
            return "Discord отказал в изменении доступа или перемещении участника."
        return "Готово. Персональные разрешения обновлены."

    async def delete_room(self, interaction: discord.Interaction, channel_id: int) -> str:
        room = await self._authorized(interaction, channel_id)
        if room is None:
            return "Нет доступа к этой комнате."
        channel = interaction.guild.get_channel(channel_id)
        if channel is None:
            await store.remove(self.bot.database, interaction.guild_id, channel_id)
            return "Готово. Запись удалённой комнаты очищена."
        if not isinstance(channel, discord.VoiceChannel):
            return "Невозможно закрыть этот канал."
        if any(m.id != interaction.user.id for m in channel.members):
            return "В комнате есть другие участники. Попросите их выйти перед закрытием."
        try:
            await channel.delete(reason=f"[NSB-VOICE] Закрытие владельцем {interaction.user.id}")
        except discord.HTTPException:
            log.exception("Ошибка удаления комнаты channel=%s owner=%s", channel_id, interaction.user.id)
            return "Discord не разрешил удалить комнату. Проверьте права бота."
        await store.remove(self.bot.database, interaction.guild_id, channel_id)
        return "Готово. Комната удалена."

    @voice_commands.command(name="diagnose", description="Проверить, почему не создаются голосовые комнаты")
    async def diagnose(self, interaction: discord.Interaction) -> None:
        if not can_manage_settings(interaction, self.bot.config.owner_ids):
            await interaction.response.send_message(embed=notice("Диагностика доступна администрации.",
                                                     kind="warning"), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        settings = await self._settings()
        guild = interaction.guild
        lobby = guild.get_channel(settings["lobby_channel_id"]) if settings["lobby_channel_id"] else None
        category = guild.get_channel(settings["category_channel_id"]) if settings["category_channel_id"] else None
        role_id = await self.bot.database.get_setting(guild.id, "member_role_id")
        issues = []
        if not settings["enabled"]:
            issues.append("Модуль временных комнат выключен: /voice configure.")
        if not isinstance(lobby, discord.VoiceChannel):
            issues.append("Не настроен голосовой канал создания: /voice setup.")
        if not isinstance(category, discord.CategoryChannel):
            issues.append("Не настроена категория комнат: /voice setup.")
        if guild.get_role(role_id) is None:
            issues.append("Роль участника не назначена: /botsettings → Роли. "
                          "Администрация может создать приватную комнату и без неё.")
        if isinstance(lobby, discord.VoiceChannel) and isinstance(category, discord.CategoryChannel):
            issues.extend(self._creation_problems(guild, lobby, category))
        room_count = len(await store.all_rooms(self.bot.database, guild.id))
        if room_count >= int(settings["max_rooms"]):
            issues.append("Достигнут лимит временных комнат: /voice configure.")
        approved = await self._approved(interaction.user)
        if not approved:
            issues.append("Ваш аккаунт не одобрен Membership и не имеет прав администрации.")
        inside = bool(interaction.user.voice and interaction.user.voice.channel == lobby)
        text = ("**Модуль:** " + ("включён" if settings["enabled"] else "отключён")
                + f"\\n**Генератор:** {f'<#{lobby.id}>' if lobby else 'не найден'}"
                + f"\\n**Категория:** {category.name if category else 'не найдена'}"
                + f"\\n**Активные комнаты:** {room_count}/{settings['max_rooms']}"
                + f"\\n**Вы допущены:** {'да' if approved else 'нет'}"
                + f"\\n**Вы в генераторе:** {'да' if inside else 'нет'}\\n\\n"
                + ("**Что исправить:**\\n" + "\\n".join(f"• {item}" for item in issues)
                   if issues else "**Проверка пройдена.** Войдите в генератор или используйте /voice retry."))
        await interaction.followup.send(embed=card("NewSquad / Диагностика голосовых комнат", text),
                                        ephemeral=True)

    @voice_commands.command(name="retry", description="Повторить создание комнаты, находясь в канале генератора")
    async def retry(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id != self.bot.config.guild_id or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(embed=notice("Только на NewSquad.", kind="warning"),
                                                    ephemeral=True)
            return
        settings = await self._settings()
        guild = interaction.guild
        lobby = guild.get_channel(settings["lobby_channel_id"]) if settings["lobby_channel_id"] else None
        if not isinstance(lobby, discord.VoiceChannel) or not interaction.user.voice or \
                interaction.user.voice.channel != lobby:
            await interaction.response.send_message(embed=notice(
                "Зайдите в настроенный канал создания и повторите команду.", kind="warning"), ephemeral=True)
            return
        if not await self._approved(interaction.user):
            await interaction.response.send_message(embed=notice(
                "Нет одобренной заявки Membership или права администратора.", kind="warning"), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self._create_for(interaction.user, lobby)
        await interaction.followup.send(embed=notice(
            "Попытка создания комнаты выполнена. Если перенос не произошёл, "
            "администратор может проверить причину через /voice diagnose.", kind="info"), ephemeral=True)

    @voice_commands.command(name="setup", description="Назначить канал входа и категорию временных комнат")
    async def setup_command(self, interaction: discord.Interaction, lobby: discord.VoiceChannel,
                            category: discord.CategoryChannel) -> None:
        if not can_manage_settings(interaction, self.bot.config.owner_ids):
            await interaction.response.send_message(embed=notice("Нужны права администратора.", kind="warning"),
                                                    ephemeral=True)
            return
        if lobby.guild.id != self.bot.config.guild_id or category.guild.id != self.bot.config.guild_id:
            await interaction.response.send_message(embed=notice("Выберите каналы NewSquad.", kind="warning"),
                                                    ephemeral=True)
            return
        for key, value in {"voice_lobby_channel_id": lobby.id, "voice_category_channel_id": category.id,
                           "voice_enabled": True}.items():
            await self.bot.database.set_setting(interaction.guild_id, key, value, actor_id=interaction.user.id)
        await interaction.response.send_message(embed=card("Голосовые комнаты настроены",
            f"Канал создания: {lobby.mention}\nКатегория комнат: **{category.name}**\n"
            "Участник заходит в канал создания — бот переносит его в личную комнату."), ephemeral=True)

    @voice_commands.command(name="settings", description="Текущие настройки голосовых комнат")
    async def settings_command(self, interaction: discord.Interaction) -> None:
        if not can_manage_settings(interaction, self.bot.config.owner_ids):
            await interaction.response.send_message(embed=notice("Только администрация.", kind="warning"),
                                                    ephemeral=True)
            return
        settings = await self._settings()
        rooms = await store.all_rooms(self.bot.database, self.bot.config.guild_id)
        await interaction.response.send_message(embed=card("NewSquad  /  Голосовые комнаты",
            f"**Включены:** {'да' if settings['enabled'] else 'нет'}\n"
            f"**Канал входа:** {f'<#{settings["lobby_channel_id"]}>' if settings['lobby_channel_id'] else 'не выбран'}\n"
            f"**Категория комнат:** {settings['category_channel_id'] or 'не выбрана'}\n"
            f"**Лимит комнат:** {len(rooms)}/{settings['max_rooms']}\n"
            f"**Участников на комнату:** {settings['default_limit'] or 'без лимита'}\n"
            "Настройка: `/voice setup` и `/voice configure`."), ephemeral=True)

    @voice_commands.command(name="configure", description="Настройки лимитов и включения голосовых комнат")
    async def configure(self, interaction: discord.Interaction, enabled: bool,
                        max_rooms: app_commands.Range[int, 1, 100] = 30,
                        default_limit: app_commands.Range[int, 0, 99] = 0) -> None:
        if not can_manage_settings(interaction, self.bot.config.owner_ids):
            await interaction.response.send_message(embed=notice("Только администрация.", kind="warning"),
                                                    ephemeral=True)
            return
        for key, value in {"voice_enabled": enabled, "voice_max_rooms": max_rooms,
                           "voice_default_limit": default_limit}.items():
            await self.bot.database.set_setting(interaction.guild_id, key, value, actor_id=interaction.user.id)
        await interaction.response.send_message(embed=notice("Настройки сохранены.", kind="success"), ephemeral=True)

    @voice_commands.command(name="panel", description="Открыть кнопки управления своей комнатой")
    async def panel(self, interaction: discord.Interaction) -> None:
        room = await self._own_room(interaction)
        if room is None:
            await interaction.response.send_message(embed=notice("Сначала создайте свою комнату: зайдите в "
                "голосовой канал создания.", kind="warning"), ephemeral=True)
            return
        await interaction.response.send_message(embed=describe_room(room,
            interaction.guild.get_channel(room.channel_id)), view=RoomControls(self, room.channel_id), ephemeral=True)

    @voice_commands.command(name="info", description="Посмотреть состояние собственной голосовой комнаты")
    async def info(self, interaction: discord.Interaction) -> None:
        room = await self._own_room(interaction)
        if room is None:
            await interaction.response.send_message(embed=notice("Комната не найдена.", kind="warning"), ephemeral=True)
            return
        await interaction.response.send_message(embed=describe_room(room,
            interaction.guild.get_channel(room.channel_id)), ephemeral=True)

    @voice_commands.command(name="name", description="Переименовать свою комнату")
    async def name(self, interaction: discord.Interaction, value: str) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        room = await self._own_room(interaction)
        result = await self.rename_room(interaction, room.channel_id, value) if room else "Комната не найдена."
        await interaction.followup.send(embed=notice(result, kind="success" if result.startswith("Готово") else "warning"),
                                        ephemeral=True)

    @voice_commands.command(name="limit", description="Изменить максимальное число участников комнаты")
    async def limit(self, interaction: discord.Interaction, amount: app_commands.Range[int, 0, 99]) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        room = await self._own_room(interaction)
        result = await self.limit_room(interaction, room.channel_id, amount) if room else "Комната не найдена."
        await interaction.followup.send(embed=notice(result, kind="success" if result.startswith("Готово") else "warning"),
                                        ephemeral=True)

    @voice_commands.command(name="lock", description="Закрыть или открыть вход в комнату")
    async def lock(self, interaction: discord.Interaction, enabled: bool) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        room = await self._own_room(interaction)
        result = await self.visibility(interaction, room.channel_id, "locked", enabled) if room else "Комната не найдена."
        await interaction.followup.send(embed=notice(result, kind="success" if result.startswith("Готово") else "warning"),
                                        ephemeral=True)

    @voice_commands.command(name="hide", description="Скрыть комнату от других участников")
    async def hide(self, interaction: discord.Interaction, enabled: bool) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        room = await self._own_room(interaction)
        result = await self.visibility(interaction, room.channel_id, "hidden", enabled) if room else "Комната не найдена."
        await interaction.followup.send(embed=notice(result, kind="success" if result.startswith("Готово") else "warning"),
                                        ephemeral=True)

    @voice_commands.command(name="allow", description="Разрешить вход участнику")
    async def allow(self, interaction: discord.Interaction, member: discord.Member) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        room = await self._own_room(interaction)
        result = await self.access_room(interaction, room.channel_id, member, "allow") if room else "Комната не найдена."
        await interaction.followup.send(embed=notice(result, kind="success" if result.startswith("Готово") else "warning"),
                                        ephemeral=True)

    @voice_commands.command(name="deny", description="Запретить вход участнику")
    async def deny(self, interaction: discord.Interaction, member: discord.Member) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        room = await self._own_room(interaction)
        result = await self.access_room(interaction, room.channel_id, member, "deny") if room else "Комната не найдена."
        await interaction.followup.send(embed=notice(result, kind="success" if result.startswith("Готово") else "warning"),
                                        ephemeral=True)

    @voice_commands.command(name="reset-access", description="Сбросить личное исключение доступа")
    async def reset_access(self, interaction: discord.Interaction, member: discord.Member) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        room = await self._own_room(interaction)
        result = await self.access_room(interaction, room.channel_id, member, None) if room else "Комната не найдена."
        await interaction.followup.send(embed=notice(result, kind="success" if result.startswith("Готово") else "warning"),
                                        ephemeral=True)

    @voice_commands.command(name="kick", description="Удалить участника из своей голосовой комнаты")
    async def kick(self, interaction: discord.Interaction, member: discord.Member) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        room = await self._own_room(interaction)
        if room is None or not await self._authorized(interaction, room.channel_id):
            result = "Комната не найдена или у вас нет прав."
        elif member.id == room.owner_id or member.guild_permissions.administrator or member.id == member.guild.owner_id:
            result = "Нельзя отключить владельца или администратора сервера."
        elif member.voice is None or member.voice.channel is None or member.voice.channel.id != room.channel_id:
            result = "Участник не находится в вашей комнате."
        else:
            try:
                await member.move_to(None, reason=f"NewSquad: отключён владельцем комнаты {interaction.user.id}")
            except discord.HTTPException:
                log.exception("Не удалось отключить участника room=%s user=%s", room.channel_id, member.id)
                result = "Discord отказал в переносе участника."
            else:
                result = "Готово. Участник отключён от комнаты."
        await interaction.followup.send(embed=notice(result, kind="success" if result.startswith("Готово") else "warning"),
                                        ephemeral=True)

    @voice_commands.command(name="transfer", description="Передать комнату другому участнику")
    async def transfer_command(self, interaction: discord.Interaction, member: discord.Member) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        room = await self._own_room(interaction)
        if room is None:
            result = "Комната не найдена."
        elif member.voice is None or member.voice.channel.id != room.channel_id or not await self._approved(member):
            result = "Получатель должен быть одобренным участником и находиться в этой комнате."
        else:
            try:
                await self._transfer_owner(interaction.guild, room, member)
            except ValueError as exc:
                result = str(exc)
            except discord.HTTPException:
                result = "Discord отказал в передаче прав."
            else:
                result = f"Готово. Владелец комнаты — {member.mention}."
        await interaction.followup.send(embed=notice(result, kind="success" if result.startswith("Готово") else "warning"),
                                        ephemeral=True)

    @voice_commands.command(name="claim", description="Взять управление комнатой без активного владельца")
    async def claim(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        member = interaction.user
        if not isinstance(member, discord.Member) or not await self._approved(member) or not member.voice:
            result = "Для получения комнаты нужно находиться в ней и иметь роль участника."
        else:
            channel = member.voice.channel
            room = await store.by_channel(self.bot.database, interaction.guild_id, channel.id)
            if room is None or any(u.id == room.owner_id for u in channel.members):
                result = "Комната отсутствует или её владелец ещё находится внутри."
            else:
                try:
                    await self._transfer_owner(interaction.guild, room, member)
                except ValueError as exc:
                    result = str(exc)
                except discord.HTTPException:
                    result = "Discord отклонил передачу."
                else:
                    result = "Готово. Теперь вы владелец комнаты."
        await interaction.followup.send(embed=notice(result, kind="success" if result.startswith("Готово") else "warning"),
                                        ephemeral=True)

    @voice_commands.command(name="close", description="Закрыть собственную голосовую комнату")
    async def close(self, interaction: discord.Interaction) -> None:
        room = await self._own_room(interaction)
        if room is None:
            await interaction.response.send_message(embed=notice("Комната не найдена.", kind="warning"), ephemeral=True)
            return
        await interaction.response.send_message(embed=notice("Подтвердите удаление комнаты. Убедитесь, что "
            "остальные участники вышли.", kind="warning"), view=CloseRoomConfirm(self, room.channel_id), ephemeral=True)


async def setup(bot) -> None:
    await bot.add_cog(Voice(bot), guild=discord.Object(id=bot.config.guild_id))
