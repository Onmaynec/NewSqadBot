"""Валюта NS Coins, ежедневные награды, переводы и магазин сообщества."""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from app.core.permissions import can_manage_settings
from app.services import economy as store
from app.services.audit import log_event
from app.services.membership_access import approved_member
from app.services.role_rules import forbidden_role_ids, role_safe_for_self_assign
from app.ui.mentions import role_mention
from app.ui.theme import card, notice

log = logging.getLogger(__name__)


class Economy(commands.Cog):
    economy = app_commands.Group(name="economy", description="Экономика NewSquad")
    shop = app_commands.Group(name="shop", description="Магазин NewSquad")
    inventory = app_commands.Group(name="inventory", description="Купленные предметы и титулы")

    def __init__(self, bot):
        self.bot = bot
        bot.events.subscribe("leveling.xp_awarded", "economy.activity", self.on_activity)
        bot.events.subscribe("leveling.level_up", "economy.level_reward", self.on_level_up)

    async def allowed(self, member: discord.Member) -> bool:
        if member.guild.id != self.bot.config.guild_id or member.bot:
            return False
        role_id = await self.bot.database.get_setting(member.guild.id, "candidate_role_id")
        return await approved_member(self.bot.database, member, role_id)

    async def member_check(self, interaction: discord.Interaction) -> bool:
        if (interaction.guild_id == self.bot.config.guild_id
                and isinstance(interaction.user, discord.Member)
                and await self.allowed(interaction.user)):
            return True
        await interaction.response.send_message(embed=notice(
            "Экономика доступна только одобренным участникам NewSquad.", kind="warning"), ephemeral=True)
        return False

    async def enabled(self, guild_id: int) -> bool:
        return bool(await self.bot.database.get_setting(guild_id, "economy_enabled", True))

    async def economy_check(self, interaction: discord.Interaction) -> bool:
        if not await self.member_check(interaction):
            return False
        if not await self.enabled(interaction.guild_id):
            await interaction.response.send_message(embed=notice("Экономика сейчас отключена.", kind="warning"),
                                                    ephemeral=True)
            return False
        return True

    async def admin(self, interaction: discord.Interaction) -> bool:
        if interaction.guild_id == self.bot.config.guild_id and can_manage_settings(
                interaction, self.bot.config.owner_ids):
            return True
        await interaction.response.send_message(embed=notice("Требуются права администратора.", kind="warning"),
                                                ephemeral=True)
        return False

    async def _role_safe(self, guild: discord.Guild, role: discord.Role) -> bool:
        from app.panels.botsettings import ROLE_SLOTS
        settings = {key: await self.bot.database.get_setting(guild.id, key) for key, _ in ROLE_SLOTS}
        return (role.guild.id == guild.id and
                role_safe_for_self_assign(role, guild.me, forbidden_role_ids(settings)))

    async def _deliver_role(self, member: discord.Member, item_id: int) -> bool:
        item = await store.item_by_id(self.bot.database, member.guild.id, item_id)
        if not item or item.kind != "role" or not item.role_id:
            return False
        role = member.guild.get_role(item.role_id)
        if role is None or not await self._role_safe(member.guild, role):
            log.warning("Товар #%s: роль недоступна либо небезопасна; guild=%s user=%s",
                        item_id, member.guild.id, member.id)
            return False
        try:
            if role not in member.roles:
                await member.add_roles(role, reason=f"NewSqadBot: покупка #{item.id}")
        except discord.HTTPException:
            log.exception("Выдача купленной роли не удалась guild=%s user=%s item=%s",
                          member.guild.id, member.id, item.id)
            return False
        await store.mark_role_delivered(self.bot.database, member.guild.id, member.id, item.id)
        return True

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        guild = self.bot.get_guild(self.bot.config.guild_id)
        if not guild:
            return
        pending = await store.pending_roles(self.bot.database, guild.id)
        for owned, _ in pending:
            member = guild.get_member(owned.user_id)
            if member and await self.allowed(member):
                try:
                    await self._deliver_role(member, owned.item_id)
                except Exception:
                    log.exception("Восстановление выдачи роли guild=%s user=%s item=%s",
                                  guild.id, owned.user_id, owned.item_id)

    async def on_activity(self, payload: dict) -> None:
        guild_id = int(payload["guild_id"])
        if guild_id != self.bot.config.guild_id or not await self.enabled(guild_id):
            return
        if not await self.bot.database.get_setting(guild_id, "economy_activity_enabled", True):
            return
        source = str(payload["source"])
        if source not in {"message", "voice"}:
            return
        guild = self.bot.get_guild(guild_id)
        member = guild.get_member(int(payload["user_id"])) if guild else None
        if member is None or not await self.allowed(member):
            return
        amount = int(await self.bot.database.get_setting(
            guild_id, "economy_message_reward" if source == "message" else "economy_voice_reward",
            2 if source == "message" else 5))
        if not 0 <= amount <= 1000 or amount == 0:
            return
        await store.credit_once(self.bot.database, guild_id=guild_id, user_id=member.id,
                                amount=amount, kind="activity",
                                reference=f"activity:{source}:{member.id}:{payload['reference']}")

    async def on_level_up(self, payload: dict) -> None:
        guild_id = int(payload["guild_id"])
        if guild_id != self.bot.config.guild_id or not await self.enabled(guild_id):
            return
        guild = self.bot.get_guild(guild_id)
        member = guild.get_member(int(payload["user_id"])) if guild else None
        if member is None or not await self.allowed(member):
            return
        amount = int(await self.bot.database.get_setting(guild_id, "economy_level_reward", 25))
        if not 0 <= amount <= 1000 or amount == 0:
            return
        await store.credit_once(self.bot.database, guild_id=guild_id, user_id=member.id,
                                amount=amount, kind="level",
                                reference=f"level:{member.id}:{int(payload['level'])}")

    @app_commands.command(name="balance", description="Посмотреть баланс NS Coins")
    async def balance(self, interaction: discord.Interaction, user: discord.Member | None = None) -> None:
        if not await self.economy_check(interaction):
            return
        member = user or interaction.user
        if not await self.allowed(member):
            await interaction.response.send_message(embed=notice("Этот пользователь не участник NewSquad.",
                                                                  kind="warning"), ephemeral=True)
            return
        row = await store.wallet(self.bot.database, interaction.guild_id, member.id)
        amount = row.balance if row else 0
        embed = card(f"Кошелёк  /  {discord.utils.escape_markdown(member.display_name)}",
                     f"**{amount:,} {store.CURRENCY}**\n"
                     f"Всего заработано: {row.earned if row else 0:,}\n"
                     f"Всего потрачено: {row.spent if row else 0:,}")
        embed.set_thumbnail(url=member.display_avatar.url)
        await interaction.response.send_message(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(name="daily", description="Получить ежедневную награду NS Coins")
    async def daily(self, interaction: discord.Interaction) -> None:
        if not await self.economy_check(interaction):
            return
        base = int(await self.bot.database.get_setting(interaction.guild_id, "economy_daily_reward", 100))
        try:
            result = await store.claim_daily(self.bot.database, guild_id=interaction.guild_id,
                                             user_id=interaction.user.id, timezone=self.bot.config.timezone,
                                             base=base)
        except ValueError as exc:
            await interaction.response.send_message(embed=notice(str(exc), kind="warning"), ephemeral=True)
            return
        await interaction.response.send_message(embed=card("Ежедневная награда",
            f"Начислено **+{result.amount} {store.CURRENCY}**\n"
            f"Серия посещений: **{result.streak} из 7**\nБаланс: **{result.balance:,}**"), ephemeral=True)

    @app_commands.command(name="pay", description="Перевести NS Coins другому участнику")
    async def pay(self, interaction: discord.Interaction, user: discord.Member,
                  amount: app_commands.Range[int, 1, 100000]) -> None:
        if not await self.economy_check(interaction):
            return
        if not await self.bot.database.get_setting(interaction.guild_id, "economy_transfers_enabled", True):
            await interaction.response.send_message(embed=notice("Переводы отключены.", kind="warning"), ephemeral=True)
            return
        if not await self.allowed(user):
            await interaction.response.send_message(embed=notice("Получатель должен состоять в NewSquad.",
                                                                  kind="warning"), ephemeral=True)
            return
        try:
            result = await store.transfer(self.bot.database, guild_id=interaction.guild_id,
                sender=interaction.user.id, recipient=user.id, amount=amount,
                reference=f"transfer:{interaction.id}")
        except ValueError as exc:
            await interaction.response.send_message(embed=notice(str(exc), kind="warning"), ephemeral=True)
            return
        await interaction.response.send_message(embed=notice(
            f"Переведено {amount:,} {store.CURRENCY} → {user.display_name}. "
            f"Ваш баланс: {result.balance:,}.", kind="success"), ephemeral=True)

    @economy.command(name="top", description="Рейтинг участников по балансу")
    async def leaderboard(self, interaction: discord.Interaction) -> None:
        if not await self.economy_check(interaction):
            return
        guild = interaction.guild
        entries = await store.top(self.bot.database, guild.id, limit=25)
        members = [(x, guild.get_member(x.user_id)) for x in entries]
        members = [(x, m) for x, m in members if m and await self.allowed(m)][:10]
        text = "\n".join(f"**{i}.** {discord.utils.escape_markdown(m.display_name)} — "
                         f"{w.balance:,} {store.CURRENCY}" for i, (w, m) in enumerate(members, 1))
        await interaction.response.send_message(embed=card("NewSquad  /  Богатейшие участники",
            text or "Рейтинг пока пуст."), allowed_mentions=discord.AllowedMentions.none())

    @economy.command(name="history", description="Последние изменения своего баланса")
    async def history(self, interaction: discord.Interaction) -> None:
        if not await self.economy_check(interaction):
            return
        rows = await store.ledger(self.bot.database, interaction.guild_id, interaction.user.id)
        lines = [f"{'+' if x.amount > 0 else ''}{x.amount} · {x.kind} · <t:{x.created_at}:d>"
                 for x in rows]
        await interaction.response.send_message(embed=card("История кошелька", "\n".join(lines)
                                            or "Операций пока не было."), ephemeral=True)

    @economy.command(name="settings", description="Настройки экономики NewSquad")
    async def settings(self, interaction: discord.Interaction) -> None:
        if not await self.admin(interaction):
            return
        guild_id = interaction.guild_id
        opts = (("economy_enabled", "Экономика", True),
                ("economy_transfers_enabled", "Переводы", True),
                ("economy_activity_enabled", "Награды за XP", True),
                ("economy_daily_reward", "Ежедневная награда", 100),
                ("economy_message_reward", "За сообщение с XP", 2),
                ("economy_voice_reward", "За 10 голосовых минут", 5),
                ("economy_level_reward", "За новый уровень", 25))
        lines = [f"**{label}:** {await self.bot.database.get_setting(guild_id, key, default)}"
                 for key, label, default in opts]
        await interaction.response.send_message(embed=card("NewSquad  /  Экономика", "\n".join(lines)),
                                                ephemeral=True)

    @economy.command(name="switch", description="Включить или отключить экономику и переводы")
    @app_commands.choices(feature=[
        app_commands.Choice(name="Экономика", value="enabled"),
        app_commands.Choice(name="Переводы", value="transfers_enabled"),
        app_commands.Choice(name="Награды за активность", value="activity_enabled"),
    ])
    async def switch(self, interaction: discord.Interaction, feature: app_commands.Choice[str],
                     enabled: bool) -> None:
        if not await self.admin(interaction):
            return
        await self.bot.database.set_setting(interaction.guild_id, f"economy_{feature.value}", enabled,
                                            actor_id=interaction.user.id)
        await interaction.response.send_message(embed=notice("Настройка обновлена.", kind="success"), ephemeral=True)

    @economy.command(name="rates", description="Установить награды NS Coins")
    async def rates(self, interaction: discord.Interaction,
                    daily: app_commands.Range[int, 1, 1000],
                    message: app_commands.Range[int, 0, 1000],
                    voice: app_commands.Range[int, 0, 1000],
                    level: app_commands.Range[int, 0, 1000]) -> None:
        if not await self.admin(interaction):
            return
        values = {"daily_reward": daily, "message_reward": message,
                  "voice_reward": voice, "level_reward": level}
        for key, value in values.items():
            await self.bot.database.set_setting(interaction.guild_id, f"economy_{key}", value,
                                                actor_id=interaction.user.id)
        await interaction.response.send_message(embed=notice("Награды настроены.", kind="success"), ephemeral=True)

    @economy.command(name="adjust", description="Начислить или списать валюту (для администрации)")
    async def adjust(self, interaction: discord.Interaction, user: discord.Member,
                     delta: app_commands.Range[int, -1000000, 1000000], reason: str) -> None:
        if not await self.admin(interaction):
            return
        if delta == 0 or not await self.allowed(user):
            await interaction.response.send_message(embed=notice("Сумма не должна быть нулём; нужен член NewSquad.",
                                                                  kind="warning"), ephemeral=True)
            return
        try:
            result = await store.adjust(self.bot.database, guild_id=interaction.guild_id,
                user_id=user.id, delta=delta, actor_id=interaction.user.id,
                reference=f"admin:{interaction.id}")
        except ValueError as exc:
            await interaction.response.send_message(embed=notice(str(exc), kind="warning"), ephemeral=True)
            return
        await log_event(self.bot, channel_setting="audit_channel_id", title="Корректировка валюты",
                        details=f"Участник: {user.id}; сумма: {delta}; причина: {reason[:300]}",
                        actor_id=interaction.user.id, action="economy.adjust", target_id=user.id)
        await interaction.response.send_message(embed=notice(
            f"Баланс пользователя обновлён: {result.balance:,} {store.CURRENCY}.", kind="success"), ephemeral=True)

    @shop.command(name="list", description="Каталог товаров NewSquad")
    async def catalog(self, interaction: discord.Interaction) -> None:
        if not await self.economy_check(interaction):
            return
        products = await store.items(self.bot.database, interaction.guild_id)
        lines = [f"**#{x.id} · {discord.utils.escape_markdown(x.title)}** — {x.price:,} {store.CURRENCY}"
                 f" · {'Титул' if x.kind == 'title' else 'Discord-роль'}"
                 + (f" · осталось {x.stock - x.sold}" if x.stock is not None else "")
                 for x in products[:20]]
        await interaction.response.send_message(embed=card("Магазин NewSquad", "\n".join(lines)
            or "Товаров пока нет."), allowed_mentions=discord.AllowedMentions.none())

    @shop.command(name="create", description="Добавить титул или декоративную роль в магазин")
    @app_commands.choices(kind=[app_commands.Choice(name="Титул профиля", value="title"),
                                app_commands.Choice(name="Discord-роль", value="role")])
    async def create(self, interaction: discord.Interaction, code: str, title: str,
                     kind: app_commands.Choice[str], price: app_commands.Range[int, 1, 1000000],
                     description: str = "", role: discord.Role | None = None,
                     stock: app_commands.Range[int, 1, 1000000] | None = None) -> None:
        if not await self.admin(interaction):
            return
        if kind.value == "role":
            if role is None or not await self._role_safe(interaction.guild, role):
                await interaction.response.send_message(embed=notice(
                    "Укажи безопасную декоративную роль, расположенную ниже роли бота.", kind="warning"),
                    ephemeral=True)
                return
        elif role is not None:
            await interaction.response.send_message(embed=notice("Титулу не нужна Discord-роль.", kind="warning"),
                                                    ephemeral=True)
            return
        try:
            item = await store.add_item(self.bot.database, guild_id=interaction.guild_id, code=code,
                title=title, kind=kind.value, price=price, description=description,
                role_id=role.id if role else None, stock=stock)
        except ValueError as exc:
            await interaction.response.send_message(embed=notice(str(exc), kind="warning"), ephemeral=True)
            return
        await interaction.response.send_message(embed=notice(f"Товар #{item.id} добавлен.", kind="success"),
                                                ephemeral=True)

    @shop.command(name="disable", description="Снять товар с продажи (предметы у покупателей сохранятся)")
    async def disable(self, interaction: discord.Interaction, item_id: int, active: bool = False) -> None:
        if not await self.admin(interaction):
            return
        result = await store.set_item_active(self.bot.database, interaction.guild_id, item_id, active)
        await interaction.response.send_message(embed=notice(
            "Доступность товара обновлена." if result else "Товар не найден.",
            kind="success" if result else "warning"), ephemeral=True)

    @shop.command(name="buy", description="Купить товар из магазина")
    async def buy(self, interaction: discord.Interaction, item_id: int) -> None:
        if not await self.economy_check(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        item = await store.item_by_id(self.bot.database, interaction.guild_id, item_id)
        if item and item.kind == "role":
            role = interaction.guild.get_role(item.role_id) if item.role_id else None
            if role is None or not await self._role_safe(interaction.guild, role):
                await interaction.followup.send(embed=notice(
                    "Эта роль больше недоступна для покупки. Средства не списаны.", kind="warning"), ephemeral=True)
                return
        try:
            await store.purchase(self.bot.database, guild_id=interaction.guild_id,
                                 user_id=interaction.user.id, item_id=item_id)
        except ValueError as exc:
            await interaction.followup.send(embed=notice(str(exc), kind="warning"), ephemeral=True)
            return
        delivered = True
        if item and item.kind == "role":
            delivered = await self._deliver_role(interaction.user, item.id)
        message = (f"Приобретён товар **{discord.utils.escape_markdown(item.title)}**. "
                   + (f"Роль {role_mention(role)} выдана." if item.kind == "role" and delivered else
                      "Выдача роли ожидает повторной попытки: `/inventory claim`." if not delivered else
                      "Используй `/inventory equip`, чтобы установить титул."))
        await interaction.followup.send(embed=notice(message, kind="success" if delivered else "warning"),
                                        ephemeral=True)

    @inventory.command(name="list", description="Мои купленные предметы")
    async def inventory_list(self, interaction: discord.Interaction) -> None:
        if not await self.economy_check(interaction):
            return
        rows = await store.inventory(self.bot.database, interaction.guild_id, interaction.user.id)
        entries = [f"**#{item.id} · {discord.utils.escape_markdown(item.title)}** — "
                   f"{'выбран' if owned.equipped else 'в ожидании выдачи' if owned.delivery == 'pending' else 'в инвентаре'}"
                   for owned, item in rows]
        await interaction.response.send_message(embed=card("Инвентарь NewSquad", "\n".join(entries)
            or "Пока пуст. Посмотри `/shop list`."), ephemeral=True)

    @inventory.command(name="equip", description="Выбрать купленный титул для профиля")
    async def equip(self, interaction: discord.Interaction, item_id: int) -> None:
        if not await self.economy_check(interaction):
            return
        try:
            await store.equip_title(self.bot.database, interaction.guild_id, interaction.user.id, item_id)
        except ValueError as exc:
            await interaction.response.send_message(embed=notice(str(exc), kind="warning"), ephemeral=True)
            return
        await interaction.response.send_message(embed=notice("Титул установлен.", kind="success"), ephemeral=True)

    @inventory.command(name="unequip", description="Убрать титул из профиля")
    async def unequip(self, interaction: discord.Interaction) -> None:
        if not await self.economy_check(interaction):
            return
        await store.equip_title(self.bot.database, interaction.guild_id, interaction.user.id, None)
        await interaction.response.send_message(embed=notice("Титул снят.", kind="success"), ephemeral=True)

    @inventory.command(name="claim", description="Повторить выдачу купленной Discord-роли")
    async def claim(self, interaction: discord.Interaction) -> None:
        if not await self.economy_check(interaction):
            return
        pending = await store.pending_roles(self.bot.database, interaction.guild_id, interaction.user.id)
        if not pending:
            await interaction.response.send_message(embed=notice("Нет ролей, ожидающих выдачи."), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        completed = 0
        for owned, _ in pending:
            if await self._deliver_role(interaction.user, owned.item_id):
                completed += 1
        await interaction.followup.send(embed=notice(
            f"Выдано ролей: {completed} из {len(pending)}.", kind="success" if completed else "warning"),
            ephemeral=True)


async def setup(bot) -> None:
    await bot.add_cog(Economy(bot), guild=discord.Object(id=bot.config.guild_id))
