"""Self-assigned roles, persistent panels, temporary grants and progression rewards."""
from __future__ import annotations

import asyncio
import logging
import time

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from app.core.durations import parse_duration
from app.core.permissions import can_manage_settings
from app.models import LevelRoleReward, RolePanel, RolePanelOption, TemporaryRole
from app.services.audit import log_event
from app.services.membership_access import approved_member
from app.services.role_rules import (
    forbidden_role_ids,
    role_safe_for_self_assign,
    validate_panel,
)
from app.ui.theme import BRAND_BLUE, LoggedView

log = logging.getLogger(__name__)
PROTECTED_SLOTS = (
    "member_role_id", "candidate_role_id", "female_role_id", "admin_role_id",
    "moderator_role_id", "reviewer_role_id", "support_role_id",
)


def panel_embed(panel: RolePanel) -> discord.Embed:
    e = discord.Embed(title=f"NewSquad  /  {discord.utils.escape_mentions(panel.title)}",
        description=discord.utils.escape_mentions(panel.description), color=BRAND_BLUE)
    e.set_footer(text="NewSquad · Выбранные роли можно изменить в этом меню")
    return e


class RoleSelect(discord.ui.Select):
    def __init__(self, cog: "Roles", panel: RolePanel, options: list[RolePanelOption]):
        self.cog, self.panel_id = cog, panel.id
        super().__init__(
            placeholder="Выберите роли" if panel.mode == "multi" else "Выберите одну роль",
            min_values=1, max_values=len(options) if panel.mode == "multi" else 1,
            custom_id=f"newsqad:roles:select:{panel.id}",
            options=[discord.SelectOption(label=o.label, value=str(o.role_id)) for o in options],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.cog.apply_selection(interaction, self.panel_id, set(map(int, self.values)))


class ClearRoles(discord.ui.Button):
    def __init__(self, cog: "Roles", panel_id: int):
        super().__init__(label="Снять роли этой панели", style=discord.ButtonStyle.secondary,
            custom_id=f"newsqad:roles:clear:{panel_id}")
        self.cog, self.panel_id = cog, panel_id

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.cog.apply_selection(interaction, self.panel_id, set())


class RolePanelView(LoggedView):
    def __init__(self, cog: "Roles", panel: RolePanel, options: list[RolePanelOption]):
        validate_panel(panel.mode, len(options))
        super().__init__(timeout=None)
        self.add_item(RoleSelect(cog, panel, options))
        self.add_item(ClearRoles(cog, panel.id))


class Roles(commands.Cog):
    roles = app_commands.Group(name="roles", description="Роли участников NewSquad")
    panel = app_commands.Group(name="panel", description="Панели выбора ролей", parent=roles)
    autorole = app_commands.Group(name="autorole", description="Роли после одобрения заявки", parent=roles)
    rewards = app_commands.Group(name="rewards", description="Наградные роли за уровень", parent=roles)

    def __init__(self, bot):
        self.bot = bot
        self._member_locks: dict[int, asyncio.Lock] = {}
        bot.scheduler.register("roles.expire", self._expire_grant)
        bot.events.subscribe("membership.approved", "roles.autorole", self._approved)
        bot.events.subscribe("leveling.level_up", "roles.level_reward", self._level_up)

    async def cog_load(self) -> None:
        await self._restore_panels()

    async def _admin(self, interaction: discord.Interaction) -> bool:
        if can_manage_settings(interaction, self.bot.config.owner_ids):
            return True
        await interaction.response.send_message("Нужны права администратора NewSquad.", ephemeral=True)
        return False

    async def _protected(self, guild_id: int) -> set[int]:
        settings = {key: await self.bot.database.get_setting(guild_id, key) for key in PROTECTED_SLOTS}
        return forbidden_role_ids(settings)

    def _bot_member(self, guild: discord.Guild) -> discord.Member | None:
        return guild.me or (guild.get_member(self.bot.user.id) if self.bot.user else None)

    async def _role_allowed(self, guild: discord.Guild, role: discord.Role) -> bool:
        return role_safe_for_self_assign(role, self._bot_member(guild), await self._protected(guild.id))

    async def _approved_member(self, guild: discord.Guild, user_id: int) -> discord.Member | None:
        try:
            member = await guild.fetch_member(user_id)
        except discord.NotFound:
            return None
        member_role_id = await self.bot.database.get_setting(guild.id, "member_role_id")
        return member if await approved_member(self.bot.database, member, member_role_id) else None

    async def _panel_data(self, panel_id: int):
        async with self.bot.database.sessions() as session:
            panel = await session.get(RolePanel, panel_id)
            options = (await session.scalars(select(RolePanelOption).where(
                RolePanelOption.panel_id == panel_id).order_by(RolePanelOption.id))).all()
            return panel, list(options)

    async def _restore_panels(self) -> None:
        async with self.bot.database.sessions() as session:
            ids = (await session.scalars(select(RolePanel.id).where(
                RolePanel.guild_id == self.bot.config.guild_id,
                RolePanel.enabled.is_(True), RolePanel.message_id.is_not(None)))).all()
        for panel_id in ids:
            panel, options = await self._panel_data(panel_id)
            if not options:
                continue
            try:
                self.bot.add_view(RolePanelView(self, panel, options), message_id=panel.message_id)
            except (ValueError, TypeError):
                log.exception("Не удалось восстановить панель ролей %s", panel_id)

    async def _refresh_published(self, panel_id: int) -> bool:
        panel, options = await self._panel_data(panel_id)
        if not panel or not panel.message_id or not panel.enabled:
            return True
        guild = self.bot.get_guild(panel.guild_id)
        channel = guild.get_channel(panel.channel_id) if guild else None
        if not isinstance(channel, discord.TextChannel):
            return False
        try:
            msg = await channel.fetch_message(panel.message_id)
            if not options:
                await msg.edit(embed=panel_embed(panel), view=None, allowed_mentions=discord.AllowedMentions.none())
                return True
            view = RolePanelView(self, panel, options)
            await msg.edit(embed=panel_embed(panel), view=view, allowed_mentions=discord.AllowedMentions.none())
            self.bot.add_view(view, message_id=panel.message_id)
            return True
        except (discord.HTTPException, ValueError):
            log.exception("Не удалось обновить панель ролей %s", panel_id)
            return False

    async def apply_selection(self, interaction: discord.Interaction, panel_id: int, desired: set[int]) -> None:
        guild = interaction.guild
        if guild is None or guild.id != self.bot.config.guild_id:
            await interaction.response.send_message("Панель работает только на NewSquad.", ephemeral=True)
            return
        lock = self._member_locks.setdefault(interaction.user.id, asyncio.Lock())
        async with lock:
            panel, options = await self._panel_data(panel_id)
            if not panel or not panel.enabled or panel.guild_id != guild.id or panel.message_id != interaction.message.id:
                await interaction.response.send_message("Эта панель больше не активна.", ephemeral=True)
                return
            known = {o.role_id for o in options}
            if not desired.issubset(known) or (panel.mode == "single" and len(desired) > 1):
                await interaction.response.send_message("Выбраны неизвестные роли.", ephemeral=True)
                return
            member = await self._approved_member(guild, interaction.user.id)
            if member is None:
                await interaction.response.send_message("Выбор ролей доступен только одобренным участникам NewSquad.", ephemeral=True)
                return
            for role_id in known:
                role = guild.get_role(role_id)
                if role is None or not await self._role_allowed(guild, role):
                    await interaction.response.send_message("Настройки панели устарели или бот не может управлять одной из ролей. Сообщите администрации.", ephemeral=True)
                    return
            current = {role.id for role in member.roles} & known
            to_add = [guild.get_role(rid) for rid in desired - current]
            to_remove = [guild.get_role(rid) for rid in current - desired]
            try:
                if to_remove:
                    await member.remove_roles(*to_remove, reason=f"[NSB-PANEL-{panel_id}] Выбор пользователя")
                if to_add:
                    await member.add_roles(*to_add, reason=f"[NSB-PANEL-{panel_id}] Выбор пользователя")
            except discord.HTTPException as exc:
                await interaction.response.send_message(f"Роли изменены не полностью: {exc}", ephemeral=True)
                return
            await interaction.response.send_message("Роли обновлены." if current != desired else "Изменений нет.", ephemeral=True)
            await log_event(self.bot, channel_setting="audit_channel_id", title="Выбор ролей",
                details=f"Панель #{panel_id}; добавлено: {sorted(desired-current)}; снято: {sorted(current-desired)}",
                actor_id=interaction.user.id, action="roles.panel_selection", target_id=member.id)

    async def _approved(self, payload: dict) -> None:
        guild_id, user_id = int(payload["guild_id"]), int(payload["user_id"])
        guild = self.bot.get_guild(guild_id) if guild_id == self.bot.config.guild_id else None
        if guild is None:
            raise RuntimeError("Сервер недоступен")
        member = await self._approved_member(guild, user_id)
        if member is None:
            return
        role_ids = await self.bot.database.get_setting(guild_id, "roles_auto_role_ids", [])
        for role_id in role_ids:
            role = guild.get_role(role_id)
            if role is None or not await self._role_allowed(guild, role):
                raise RuntimeError(f"Недоступна автоматическая роль {role_id}")
            if role not in member.roles:
                await member.add_roles(role, reason="[NSB-AUTOROLE] Одобрение заявки")

    async def _level_up(self, payload: dict) -> None:
        guild_id, user_id, level = int(payload["guild_id"]), int(payload["user_id"]), int(payload["level"])
        guild = self.bot.get_guild(guild_id) if guild_id == self.bot.config.guild_id else None
        if guild is None:
            raise RuntimeError("Сервер недоступен")
        member = await self._approved_member(guild, user_id)
        if member is None:
            return
        async with self.bot.database.sessions() as session:
            ids = (await session.scalars(select(LevelRoleReward.role_id).where(
                LevelRoleReward.guild_id == guild_id, LevelRoleReward.level <= level))).all()
        for rid in set(ids):
            role = guild.get_role(rid)
            if role is None or not await self._role_allowed(guild, role):
                raise RuntimeError(f"Наградная роль {rid} недоступна")
            if role not in member.roles:
                await member.add_roles(role, reason=f"[NSB-LEVEL] Достигнут уровень {level}")

    async def _expire_grant(self, payload: dict) -> None:
        grant_id = int(payload["grant_id"])
        async with self.bot.database.sessions() as session:
            grant = await session.get(TemporaryRole, grant_id)
            if not grant or grant.guild_id != self.bot.config.guild_id or grant.status != "active":
                return
            if grant.expires_at > int(time.time()):
                raise RuntimeError("Срок временной роли ещё не наступил")
            guild_id, user_id, role_id = grant.guild_id, grant.user_id, grant.role_id
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise RuntimeError("Сервер недоступен")
        role = guild.get_role(role_id)
        try:
            member = await guild.fetch_member(user_id)
        except discord.NotFound:
            member = None
        if member and role and role in member.roles:
            bot_member = self._bot_member(guild)
            if bot_member is None or bot_member.top_role <= role or not bot_member.guild_permissions.manage_roles:
                raise RuntimeError("Нет прав для снятия временной роли")
            await member.remove_roles(role, reason=f"[NSB-TEMP-{grant_id}] Срок истёк")
        async with self.bot.database.session() as session:
            await session.execute(update(TemporaryRole).where(
                TemporaryRole.id == grant_id, TemporaryRole.status == "active").values(status="expired"))

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        # Recover jobs after a process dies between role grant and scheduler registration.
        async with self.bot.database.sessions() as session:
            leases = (await session.scalars(select(TemporaryRole).where(
                TemporaryRole.guild_id == self.bot.config.guild_id, TemporaryRole.status == "active"))).all()
        for lease in leases:
            await self.bot.scheduler.schedule(job_key=f"roles:expire:{lease.id}", handler="roles.expire",
                due_at=lease.expires_at, payload={"grant_id": lease.id})

    @roles.command(name="settings", description="Настройки ролей и число панелей")
    async def settings(self, interaction: discord.Interaction) -> None:
        if not await self._admin(interaction):
            return
        async with self.bot.database.sessions() as session:
            count = len((await session.scalars(select(RolePanel.id).where(
                RolePanel.guild_id == interaction.guild_id, RolePanel.enabled.is_(True)))).all())
        auto = await self.bot.database.get_setting(interaction.guild_id, "roles_auto_role_ids", [])
        await interaction.response.send_message(f"Панелей: **{count}**\nАвтоматические роли: {', '.join(f'<@&{r}>' for r in auto) or 'нет'}",
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @panel.command(name="create", description="Создать черновик панели ролей")
    @app_commands.choices(mode=[app_commands.Choice(name="Несколько ролей", value="multi"),
                                 app_commands.Choice(name="Только одна роль", value="single")])
    async def panel_create(self, interaction: discord.Interaction, channel: discord.TextChannel,
                           title: str, mode: app_commands.Choice[str], description: str = "Выберите роли в меню ниже.") -> None:
        if not await self._admin(interaction):
            return
        if channel.guild.id != interaction.guild_id or not 1 <= len(title.strip()) <= 100 or len(description) > 1000:
            await interaction.response.send_message("Неверный канал или длина названия/описания.", ephemeral=True)
            return
        async with self.bot.database.session() as session:
            panel = RolePanel(guild_id=interaction.guild_id, channel_id=channel.id, message_id=None,
                title=title.strip(), description=description, mode=mode.value,
                enabled=True, created_by=interaction.user.id, created_at=int(time.time()))
            session.add(panel)
            await session.flush()
            number = panel.id
        await interaction.response.send_message(f"Создан черновик панели **#{number}**. Добавьте роли: `/roles panel add`, затем `/roles panel publish`.", ephemeral=True)

    @panel.command(name="add", description="Добавить роль в панель")
    async def panel_add(self, interaction: discord.Interaction, number: int, role: discord.Role, label: str) -> None:
        if not await self._admin(interaction):
            return
        panel, options = await self._panel_data(number)
        if not panel or panel.guild_id != interaction.guild_id or not panel.enabled:
            await interaction.response.send_message("Панель не найдена.", ephemeral=True)
            return
        if not await self._role_allowed(interaction.guild, role) or not 1 <= len(label.strip()) <= 80:
            await interaction.response.send_message("Роль недоступна для самостоятельной выдачи или неверная подпись.", ephemeral=True)
            return
        if len(options) >= 25:
            await interaction.response.send_message("В одной панели допускается максимум 25 ролей.", ephemeral=True)
            return
        try:
            async with self.bot.database.session() as session:
                session.add(RolePanelOption(panel_id=number, role_id=role.id, label=label.strip()))
        except IntegrityError:
            await interaction.response.send_message("Эта роль уже добавлена.", ephemeral=True)
            return
        refreshed = await self._refresh_published(number)
        await interaction.response.send_message("Роль добавлена." + ("" if refreshed else " Панель в канале не обновлена, используйте publish."), ephemeral=True)

    @panel.command(name="remove", description="Удалить роль из панели")
    async def panel_remove(self, interaction: discord.Interaction, number: int, role: discord.Role) -> None:
        if not await self._admin(interaction):
            return
        panel, options = await self._panel_data(number)
        if not panel or panel.guild_id != interaction.guild_id or role.id not in {o.role_id for o in options}:
            await interaction.response.send_message("Роль или панель не найдены.", ephemeral=True)
            return
        async with self.bot.database.session() as session:
            await session.execute(delete(RolePanelOption).where(
                RolePanelOption.panel_id == number, RolePanelOption.role_id == role.id))
        refreshed = await self._refresh_published(number)
        await interaction.response.send_message("Роль убрана из панели." + ("" if refreshed else " Требуется повторная публикация."), ephemeral=True)

    @panel.command(name="publish", description="Опубликовать или обновить панель")
    async def panel_publish(self, interaction: discord.Interaction, number: int) -> None:
        if not await self._admin(interaction):
            return
        panel, options = await self._panel_data(number)
        if not panel or panel.guild_id != interaction.guild_id or not panel.enabled:
            await interaction.response.send_message("Панель не найдена.", ephemeral=True)
            return
        try:
            validate_panel(panel.mode, len(options))
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        channel = interaction.guild.get_channel(panel.channel_id)
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("Канал панели удалён. Создайте новую панель.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        if panel.message_id:
            ok = await self._refresh_published(number)
            await interaction.followup.send("Панель обновлена." if ok else "Не удалось обновить панель. Проверьте права бота.", ephemeral=True)
            return
        view = RolePanelView(self, panel, options)
        try:
            message = await channel.send(embed=panel_embed(panel), view=view,
                allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException as exc:
            await interaction.followup.send(f"Не удалось опубликовать панель: {exc}", ephemeral=True)
            return
        async with self.bot.database.session() as session:
            await session.execute(update(RolePanel).where(RolePanel.id == number, RolePanel.message_id.is_(None)).values(message_id=message.id))
        self.bot.add_view(view, message_id=message.id)
        await interaction.followup.send(f"Панель **#{number}** опубликована: {message.jump_url}", ephemeral=True)

    @panel.command(name="list", description="Список панелей ролей")
    async def panel_list(self, interaction: discord.Interaction) -> None:
        if not await self._admin(interaction):
            return
        async with self.bot.database.sessions() as session:
            panels = (await session.scalars(select(RolePanel).where(RolePanel.guild_id == interaction.guild_id).order_by(RolePanel.id.desc()).limit(20))).all()
        lines = [f"#{p.id}: {discord.utils.escape_mentions(p.title)} — {p.mode}, {'опубликована' if p.message_id else 'черновик'}{' (отключена)' if not p.enabled else ''}" for p in panels]
        await interaction.response.send_message("\n".join(lines)[:1800] or "Панелей ещё нет.", ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none())

    @panel.command(name="welcome", description="Добавить ссылку на панель в приветствие")
    async def panel_welcome(self, interaction: discord.Interaction, number: int) -> None:
        if not await self._admin(interaction):
            return
        panel, options = await self._panel_data(number)
        if not panel or panel.guild_id != interaction.guild_id or not panel.enabled or not panel.message_id or not options:
            await interaction.response.send_message("Сначала опубликуйте действующую панель.", ephemeral=True)
            return
        await self.bot.database.set_setting(interaction.guild_id, "welcome_roles_panel_message_id", panel.message_id,
            actor_id=interaction.user.id)
        await self.bot.database.set_setting(interaction.guild_id, "welcome_roles_panel_channel_id", panel.channel_id,
            actor_id=interaction.user.id)
        await interaction.response.send_message("Ссылка на панель появится в следующих приветствиях.", ephemeral=True)

    @panel.command(name="delete", description="Отключить панель без удаления истории")
    async def panel_delete(self, interaction: discord.Interaction, number: int) -> None:
        if not await self._admin(interaction):
            return
        panel, _ = await self._panel_data(number)
        if not panel or panel.guild_id != interaction.guild_id:
            await interaction.response.send_message("Панель не найдена.", ephemeral=True)
            return
        async with self.bot.database.session() as session:
            await session.execute(update(RolePanel).where(RolePanel.id == number).values(enabled=False))
        guild = interaction.guild
        channel = guild.get_channel(panel.channel_id)
        if panel.message_id and isinstance(channel, discord.TextChannel):
            try:
                msg = await channel.fetch_message(panel.message_id)
                await msg.edit(content="Панель выбора ролей отключена.", embed=None, view=None)
            except discord.HTTPException:
                log.exception("Не удалось убрать старое меню ролей %s", number)
        await interaction.response.send_message("Панель отключена. История сохранена.", ephemeral=True)

    @roles.command(name="add", description="Выдать безопасную роль одобренному участнику")
    async def add(self, interaction: discord.Interaction, user: discord.Member, role: discord.Role) -> None:
        if not await self._admin(interaction):
            return
        if not await self._role_allowed(interaction.guild, role) or not await approved_member(self.bot.database, user, await self.bot.database.get_setting(interaction.guild_id, "member_role_id")):
            await interaction.response.send_message("Недоступная роль или участник не одобрен.", ephemeral=True)
            return
        try:
            await user.add_roles(role, reason=f"[NSB-ROLES] Выдал {interaction.user.id}")
        except discord.HTTPException as exc:
            await interaction.response.send_message(f"Выдать роль не удалось: {exc}", ephemeral=True)
            return
        await log_event(self.bot, channel_setting="audit_channel_id", title="Роль выдана вручную",
            details=f"Роль: {role.id}; участник: {user.id}", actor_id=interaction.user.id,
            action="roles.grant", target_id=user.id)
        await interaction.response.send_message("Роль выдана.", ephemeral=True)

    @roles.command(name="remove", description="Снять безопасную роль с участника")
    async def remove(self, interaction: discord.Interaction, user: discord.Member, role: discord.Role) -> None:
        if not await self._admin(interaction):
            return
        if not await self._role_allowed(interaction.guild, role):
            await interaction.response.send_message("Этой ролью нельзя управлять через модуль Roles.", ephemeral=True)
            return
        try:
            await user.remove_roles(role, reason=f"[NSB-ROLES] Снял {interaction.user.id}")
        except discord.HTTPException as exc:
            await interaction.response.send_message(f"Снять роль не удалось: {exc}", ephemeral=True)
            return
        await log_event(self.bot, channel_setting="audit_channel_id", title="Роль снята вручную",
            details=f"Роль: {role.id}; участник: {user.id}", actor_id=interaction.user.id,
            action="roles.revoke", target_id=user.id)
        await interaction.response.send_message("Роль снята.", ephemeral=True)

    @roles.command(name="temporary", description="Выдать роль на ограниченный срок (например 2h, 7d)")
    async def temporary(self, interaction: discord.Interaction, user: discord.Member, role: discord.Role, duration: str) -> None:
        if not await self._admin(interaction):
            return
        if not await self._role_allowed(interaction.guild, role) or not await approved_member(self.bot.database, user, await self.bot.database.get_setting(interaction.guild_id, "member_role_id")):
            await interaction.response.send_message("Недоступная роль или участник не одобрен.", ephemeral=True)
            return
        try:
            seconds = parse_duration(duration, allow_permanent=False, max_seconds=365 * 86400)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        if role in user.roles:
            await interaction.response.send_message("Роль уже есть у участника. Временную выдачу нельзя использовать для её автоматического снятия.", ephemeral=True)
            return
        async with self.bot.database.sessions() as session:
            existing = await session.scalar(select(TemporaryRole.id).where(
                TemporaryRole.guild_id == interaction.guild_id, TemporaryRole.user_id == user.id,
                TemporaryRole.role_id == role.id, TemporaryRole.status.in_(("active", "granting"))))
        if existing:
            await interaction.response.send_message("На эту роль уже действует временная выдача.", ephemeral=True)
            return
        expires = int(time.time()) + seconds
        async with self.bot.database.session() as session:
            grant = TemporaryRole(guild_id=interaction.guild_id, user_id=user.id, role_id=role.id,
                granted_by=interaction.user.id, granted_at=int(time.time()), expires_at=expires, status="granting")
            session.add(grant)
            await session.flush()
            grant_id = grant.id
        try:
            await user.add_roles(role, reason=f"[NSB-TEMP-{grant_id}] Выдана на {duration}")
        except discord.HTTPException as exc:
            async with self.bot.database.session() as session:
                await session.execute(update(TemporaryRole).where(TemporaryRole.id == grant_id).values(status="failed"))
            await interaction.response.send_message(f"Не удалось выдать временную роль: {exc}", ephemeral=True)
            return
        async with self.bot.database.session() as session:
            await session.execute(update(TemporaryRole).where(TemporaryRole.id == grant_id).values(status="active"))
        await self.bot.scheduler.schedule(job_key=f"roles:expire:{grant_id}", handler="roles.expire",
            due_at=expires, payload={"grant_id": grant_id})
        await log_event(self.bot, channel_setting="audit_channel_id", title="Временная роль выдана",
            details=f"Роль: {role.id}; участник: {user.id}; срок: {duration}; выдача #{grant_id}",
            actor_id=interaction.user.id, action="roles.temporary", target_id=user.id)
        await interaction.response.send_message(f"Роль выдана на {duration}. Выдача #{grant_id}.", ephemeral=True)

    @autorole.command(name="add", description="Добавить безопасную роль, выдаваемую после одобрения")
    async def autorole_add(self, interaction: discord.Interaction, role: discord.Role) -> None:
        if not await self._admin(interaction):
            return
        if not await self._role_allowed(interaction.guild, role):
            await interaction.response.send_message("Эта роль недоступна для автоматической выдачи.", ephemeral=True)
            return
        ids = await self.bot.database.get_setting(interaction.guild_id, "roles_auto_role_ids", [])
        ids = sorted(set(ids) | {role.id})
        await self.bot.database.set_setting(interaction.guild_id, "roles_auto_role_ids", ids, actor_id=interaction.user.id)
        await interaction.response.send_message("Роль добавлена в автоматическую выдачу после Membership.", ephemeral=True)

    @autorole.command(name="remove", description="Исключить роль из автоматической выдачи")
    async def autorole_remove(self, interaction: discord.Interaction, role: discord.Role) -> None:
        if not await self._admin(interaction):
            return
        ids = await self.bot.database.get_setting(interaction.guild_id, "roles_auto_role_ids", [])
        await self.bot.database.set_setting(interaction.guild_id, "roles_auto_role_ids", [i for i in ids if i != role.id], actor_id=interaction.user.id)
        await interaction.response.send_message("Роль исключена из автоматической выдачи.", ephemeral=True)

    @autorole.command(name="list", description="Показать автоматические роли")
    async def autorole_list(self, interaction: discord.Interaction) -> None:
        if not await self._admin(interaction):
            return
        ids = await self.bot.database.get_setting(interaction.guild_id, "roles_auto_role_ids", [])
        await interaction.response.send_message("\n".join(f"<@&{rid}>" for rid in ids) or "Роли не выбраны.", ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none())

    @rewards.command(name="add", description="Выдавать роль при достижении уровня")
    async def reward_add(self, interaction: discord.Interaction, level: int, role: discord.Role) -> None:
        if not await self._admin(interaction):
            return
        if not 1 <= level <= 10000 or not await self._role_allowed(interaction.guild, role):
            await interaction.response.send_message("Уровень должен быть от 1 до 10000, роль — безопасной.", ephemeral=True)
            return
        try:
            async with self.bot.database.session() as session:
                session.add(LevelRoleReward(guild_id=interaction.guild_id, level=level, role_id=role.id))
        except IntegrityError:
            await interaction.response.send_message("Такая награда уже существует.", ephemeral=True)
            return
        await interaction.response.send_message("Правило сохранено. Автоматическая выдача начнётся после подключения модуля уровней.", ephemeral=True)

    @rewards.command(name="remove", description="Удалить правило наградной роли")
    async def reward_remove(self, interaction: discord.Interaction, level: int, role: discord.Role) -> None:
        if not await self._admin(interaction):
            return
        async with self.bot.database.session() as session:
            await session.execute(delete(LevelRoleReward).where(
                LevelRoleReward.guild_id == interaction.guild_id, LevelRoleReward.level == level, LevelRoleReward.role_id == role.id))
        await interaction.response.send_message("Правило награды удалено.", ephemeral=True)

    @rewards.command(name="list", description="Список наградных ролей")
    async def reward_list(self, interaction: discord.Interaction) -> None:
        if not await self._admin(interaction):
            return
        async with self.bot.database.sessions() as session:
            records = (await session.scalars(select(LevelRoleReward).where(
                LevelRoleReward.guild_id == interaction.guild_id).order_by(LevelRoleReward.level).limit(40))).all()
        lines = [f"Уровень {r.level} → <@&{r.role_id}>" for r in records]
        await interaction.response.send_message("\n".join(lines)[:1900] or "Правил пока нет.", ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none())


async def setup(bot) -> None:
    await bot.add_cog(Roles(bot), guild=discord.Object(id=bot.config.guild_id))
