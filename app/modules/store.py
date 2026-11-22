"""NewSqadBot seller-managed store (real-currency orders, no payment execution)."""
from __future__ import annotations

import logging
from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands

from app.core.permissions import can_manage_settings
from app.modules.store_views import (
    STATE_LABELS,
    StoreBuyerOrderView,
    StoreCatalogView,
    StorePanelView,
    StoreStaffOrderView,
    order_embed,
    product_embed,
)
from app.services import store as records
from app.ui.theme import BRAND_BLUE

log = logging.getLogger(__name__)
SAFE_PAYMENT_TEXT = (
    "Свяжитесь с продавцом для получения реквизитов и условий оплаты. "
    "Никогда не передавайте пароль Discord, токены или реквизиты карт в чат."
)


class Store(commands.Cog):
    store = app_commands.Group(name="store", description="NEW SQAD SHOP · реальные товары и заказы")

    def __init__(self, bot):
        self.bot = bot
        self._restored = False
        bot.add_view(StorePanelView(self))

    def is_staff(self, interaction: discord.Interaction) -> bool:
        return (interaction.guild_id == self.bot.config.guild_id and
                can_manage_settings(interaction, self.bot.config.owner_ids))

    async def staff(self, interaction: discord.Interaction) -> bool:
        return self.is_staff(interaction)

    async def customer(self, interaction: discord.Interaction) -> bool:
        if (interaction.guild_id == self.bot.config.guild_id
                and isinstance(interaction.user, discord.Member)
                and not interaction.user.bot):
            return True
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "Магазин доступен только участникам сервера NEW SQAD.", ephemeral=True)
        return False

    async def staff_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        channel_id = await self.bot.database.get_setting(guild.id, "store_staff_channel_id")
        channel = guild.get_channel(channel_id) if channel_id else None
        if not isinstance(channel, discord.TextChannel):
            return None
        if channel.permissions_for(guild.default_role).view_channel:
            return None
        member = guild.me
        if member is None:
            return None
        perms = channel.permissions_for(member)
        if not (perms.view_channel and perms.send_messages and perms.embed_links):
            return None
        return channel

    async def ready_for_orders(self, guild: discord.Guild) -> bool:
        return (guild is not None and guild.id == self.bot.config.guild_id
                and await self.staff_channel(guild) is not None)

    async def payment_instructions(self, guild_id: int) -> str:
        return str(await self.bot.database.get_setting(
            guild_id, "store_payment_instructions", SAFE_PAYMENT_TEXT))

    async def notify_new(self, order) -> None:
        guild = self.bot.get_guild(order.guild_id)
        if guild is None:
            log.error("Магазин: сервер недоступен для заказа #%s", order.id)
            return
        channel = await self.staff_channel(guild)
        if channel is None:
            log.error("Магазин: приватный канал сотрудников недоступен, заказ #%s сохранён в БД", order.id)
            return
        view = StoreStaffOrderView(self, order.id)
        try:
            message = await channel.send(
                content=f"**Новый заказ #{order.id:05d}.** Проверьте получение оплаты самостоятельно.",
                embed=order_embed(order, staff=True), view=view,
                allowed_mentions=discord.AllowedMentions.none())
            await records.set_staff_message(self.bot.database, guild.id, order.id,
                                             channel.id, message.id)
            self.bot.add_view(view, message_id=message.id)
        except discord.HTTPException:
            log.exception("Магазин: уведомление заказа #%s не доставлено", order.id)

    async def sync_staff(self, order) -> None:
        if not order.staff_message_id or not order.staff_channel_id:
            return
        guild = self.bot.get_guild(order.guild_id)
        channel = guild.get_channel(order.staff_channel_id) if guild else None
        if not isinstance(channel, discord.TextChannel):
            return
        try:
            message = await channel.fetch_message(order.staff_message_id)
            view = (StoreStaffOrderView(self, order.id) if order.status in records.OPEN_STATES
                    else None)
            await message.edit(embed=order_embed(order, staff=True), view=view)
        except discord.HTTPException:
            log.warning("Магазин: карточка заказа #%s не обновилась; данные SQLite сохранены", order.id)

    async def notify_buyer(self, order) -> None:
        try:
            recipient = self.bot.get_user(order.user_id) or await self.bot.fetch_user(order.user_id)
            await recipient.send(
                content=f"Статус заказа #{order.id:05d}: {STATE_LABELS[order.status]}.",
                embed=order_embed(order), allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            log.info("Магазин: покупателю #%s недоступны личные сообщения", order.user_id)

    async def buyer_action(self, interaction: discord.Interaction, order_id: int, action: str) -> None:
        if not await self.customer(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            order = await records.transition(
                self.bot.database, interaction.guild_id, order_id,
                actor_id=interaction.user.id, owner_id=interaction.user.id, action=action)
        except (PermissionError, ValueError) as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        if order is None:
            await interaction.followup.send(
                "Заказ не найден или его статус уже изменён. Используйте /store order.",
                ephemeral=True)
            return
        await self.sync_staff(order)
        await interaction.followup.send(
            embed=order_embed(order),
            content=("Продавец получил сообщение о заявленной оплате. "
                     "Оплата ещё не подтверждена." if action == "report_payment"
                     else "Заказ отменён. Зарезервированный остаток восстановлен."),
            ephemeral=True)

    async def staff_action(self, interaction: discord.Interaction, order_id: int, action: str) -> None:
        if not self.is_staff(interaction):
            await interaction.response.send_message("Только администратор магазина.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            order = await records.transition(self.bot.database, interaction.guild_id,
                order_id, actor_id=interaction.user.id, action=action, staff=True)
        except (PermissionError, ValueError) as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        if order is None:
            await interaction.followup.send(
                "Заказ уже изменён или этот переход статуса невозможен.", ephemeral=True)
            return
        await self.sync_staff(order)
        await self.notify_buyer(order)
        await interaction.followup.send(embed=order_embed(order, staff=True), ephemeral=True)

    async def show_my_orders(self, interaction: discord.Interaction) -> None:
        entries = await records.orders(self.bot.database, interaction.guild_id,
                                       user_id=interaction.user.id, limit=10)
        if not entries:
            await interaction.response.send_message("У вас пока нет заказов.", ephemeral=True)
            return
        lines = [f"**#{order.id:05d}** · {discord.utils.escape_markdown(order.product_title)} "
                 f"· {records.format_money(order.price_minor, order.currency)} "
                 f"· {STATE_LABELS[order.status]}" for order in entries]
        await interaction.response.send_message("\n".join(lines)[:1900], ephemeral=True)

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._restored:
            return
        self._restored = True
        pending = await records.orders(self.bot.database, self.bot.config.guild_id, limit=200)
        restored = 0
        for order in pending:
            if order.status not in records.OPEN_STATES or not order.staff_message_id:
                continue
            self.bot.add_view(StoreStaffOrderView(self, order.id),
                              message_id=order.staff_message_id)
            restored += 1
        log.info("Магазин: восстановлены %s панелей заказов", restored)

    @store.command(name="catalog", description="Посмотреть реальные товары магазина")
    async def catalog_command(self, interaction: discord.Interaction) -> None:
        if not await self.customer(interaction):
            return
        entries = await records.products(self.bot.database, interaction.guild_id, public=True)
        if not entries:
            await interaction.response.send_message("Магазин пока пуст.", ephemeral=True)
            return
        await interaction.response.send_message(
            embed=discord.Embed(title="NEW SQAD SHOP", description=(
                "Выберите товар из каталога. Оплата и передача товаров выполняются "
                "продавцом вручную; бот только регистрирует заказ."), color=BRAND_BLUE),
            view=StoreCatalogView(self, interaction.user.id, entries), ephemeral=True)

    @store.command(name="my", description="Список ваших заказов")
    async def my_command(self, interaction: discord.Interaction) -> None:
        if await self.customer(interaction):
            await self.show_my_orders(interaction)

    @store.command(name="order", description="Статус заказа по номеру")
    async def order_command(self, interaction: discord.Interaction, number: int) -> None:
        if not await self.customer(interaction):
            return
        order = await records.order_by_id(self.bot.database, interaction.guild_id, number)
        if order is None or (order.user_id != interaction.user.id and not self.is_staff(interaction)):
            await interaction.response.send_message("Заказ не найден.", ephemeral=True)
            return
        can_edit = order.user_id == interaction.user.id and order.status in (
            "awaiting_payment", "payment_reported")
        view = (StoreBuyerOrderView(self, interaction.user.id, order.id) if can_edit else None)
        instructions = (await self.payment_instructions(interaction.guild_id)
                        if order.status == "awaiting_payment" and order.user_id == interaction.user.id
                        else "")
        await interaction.response.send_message(embed=order_embed(
            order, staff=self.is_staff(interaction)), view=view, ephemeral=True,
            content=discord.utils.escape_mentions(instructions) if instructions else None)

    @store.command(name="add", description="Добавить товар с реальной ценой")
    @app_commands.describe(price="Цена, например 399.00", stock="-1 для безлимита")
    async def add_command(self, interaction: discord.Interaction,
                          sku: str, title: str, price: str,
                          currency: Literal["RUB", "EUR", "USD"] = "RUB",
                          category: Literal["nitro_gift", "server_boost", "digital", "service", "other"] = "digital",
                          description: str = "", stock: int = -1) -> None:
        if not self.is_staff(interaction):
            await interaction.response.send_message("Только администратор магазина.", ephemeral=True)
            return
        if await records.product_by_sku(self.bot.database, interaction.guild_id, sku):
            await interaction.response.send_message(
                "Такой артикул уже существует. Измените товар через веб-консоль.", ephemeral=True)
            return
        try:
            item = await records.save_product(self.bot.database, interaction.guild_id,
                sku=sku, title=title, description=description, category=category,
                price_minor=records.parse_price(price), currency=currency,
                stock=None if stock == -1 else stock)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.send_message(embed=product_embed(item), ephemeral=True)

    @store.command(name="stock", description="Изменить доступное количество товара")
    @app_commands.describe(quantity="-1 для безлимита, 0 означает нет в наличии")
    async def stock_command(self, interaction: discord.Interaction,
                            sku: str, quantity: int) -> None:
        if not self.is_staff(interaction):
            await interaction.response.send_message("Только администратор магазина.", ephemeral=True)
            return
        item = await records.product_by_sku(self.bot.database, interaction.guild_id, sku)
        if item is None:
            await interaction.response.send_message("Товар не найден.", ephemeral=True)
            return
        try:
            saved = await records.save_product(self.bot.database, interaction.guild_id,
                sku=item.sku, title=item.title, description=item.description,
                category=item.category, price_minor=item.price_minor, currency=item.currency,
                stock=None if quantity == -1 else quantity, active=item.active,
                sort_order=item.sort_order)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.send_message(embed=product_embed(saved), ephemeral=True)

    @store.command(name="toggle", description="Показать/скрыть товар в каталоге")
    async def toggle_command(self, interaction: discord.Interaction,
                             sku: str, enabled: bool) -> None:
        if not self.is_staff(interaction):
            await interaction.response.send_message("Только администратор магазина.", ephemeral=True)
            return
        item = await records.product_by_sku(self.bot.database, interaction.guild_id, sku)
        if item is None:
            await interaction.response.send_message("Товар не найден.", ephemeral=True)
            return
        saved = await records.save_product(self.bot.database, interaction.guild_id,
            sku=item.sku, title=item.title, description=item.description,
            category=item.category, price_minor=item.price_minor, currency=item.currency,
            stock=item.stock, active=enabled, sort_order=item.sort_order)
        await interaction.response.send_message(
            f"**{discord.utils.escape_markdown(saved.title)}**: "
            f"{'доступен' if saved.active else 'скрыт'} в каталоге.", ephemeral=True)

    @store.command(name="settings", description="Приватный канал, куда приходят заказы")
    async def settings_command(self, interaction: discord.Interaction,
                               orders_channel: discord.TextChannel) -> None:
        if not self.is_staff(interaction):
            await interaction.response.send_message("Только администратор магазина.", ephemeral=True)
            return
        if orders_channel.guild.id != interaction.guild_id or (
                orders_channel.permissions_for(interaction.guild.default_role).view_channel):
            await interaction.response.send_message(
                "Выберите приватный канал, невидимый для @everyone.", ephemeral=True)
            return
        perms = orders_channel.permissions_for(interaction.guild.me)
        if not (perms.view_channel and perms.send_messages and perms.embed_links):
            await interaction.response.send_message(
                "Боту нужны View Channel, Send Messages и Embed Links в канале.", ephemeral=True)
            return
        await self.bot.database.set_setting(interaction.guild_id, "store_staff_channel_id",
            orders_channel.id, actor_id=interaction.user.id)
        await interaction.response.send_message(
            f"Заказы будут приходить в {orders_channel.mention}.", ephemeral=True)

    @store.command(name="payment", description="Инструкция покупателю по оплате (не принимать оплату ботом)")
    async def payment_command(self, interaction: discord.Interaction, instruction: str) -> None:
        if not self.is_staff(interaction):
            await interaction.response.send_message("Только администратор магазина.", ephemeral=True)
            return
        if not 20 <= len(instruction.strip()) <= 650:
            await interaction.response.send_message(
                "Инструкция: от 20 до 650 символов.", ephemeral=True)
            return
        await self.bot.database.set_setting(interaction.guild_id, "store_payment_instructions",
            instruction.strip(), actor_id=interaction.user.id)
        await interaction.response.send_message("Инструкция оплаты сохранена.", ephemeral=True)

    @store.command(name="panel", description="Опубликовать магазин в указанном канале")
    async def panel_command(self, interaction: discord.Interaction,
                            channel: discord.TextChannel) -> None:
        if not self.is_staff(interaction):
            await interaction.response.send_message("Только администратор магазина.", ephemeral=True)
            return
        if channel.guild.id != interaction.guild_id:
            await interaction.response.send_message("Чужой канал.", ephemeral=True)
            return
        if not await self.ready_for_orders(interaction.guild):
            await interaction.response.send_message(
                "Сначала настройте закрытый канал заказов: /store settings.", ephemeral=True)
            return
        embed = discord.Embed(title="NEW SQAD · МАГАЗИН",
            description=(
                "Nitro, подарочные подписки, бусты и другие товары — "
                "только при наличии разрешённых способов передачи.\n\n"
                "**Как заказать**\n"
                "1. Откройте каталог и выберите товар.\n"
                "2. Укажите количество и подтвердите заказ.\n"
                "3. Получите номер заказа и инструкцию продавца.\n"
                "4. После оплаты сообщите через кнопку «Я оплатил».\n"
                "5. Продавец проверит оплату и выполнит заказ.\n\n"
                "Никогда не передавайте пароли, токены и данные карты."),
            color=BRAND_BLUE)
        try:
            message = await channel.send(embed=embed, view=StorePanelView(self),
                allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            await interaction.response.send_message(
                "Не удалось отправить панель. Проверьте права бота.", ephemeral=True)
            return
        await records.register_panel(self.bot.database, interaction.guild_id, channel.id,
                                     message.id, interaction.user.id)
        await interaction.response.send_message(
            f"Магазин опубликован в {channel.mention}.", ephemeral=True)

    @store.command(name="orders", description="Последние заказы для продавца")
    async def orders_command(self, interaction: discord.Interaction) -> None:
        if not self.is_staff(interaction):
            await interaction.response.send_message("Только администратор магазина.", ephemeral=True)
            return
        rows = await records.orders(self.bot.database, interaction.guild_id, limit=15)
        lines = [f"**#{o.id:05d}** · <@{o.user_id}> · {discord.utils.escape_markdown(o.product_title)}"
                 f" · {records.format_money(o.price_minor, o.currency)} · {STATE_LABELS[o.status]}"
                 for o in rows]
        await interaction.response.send_message(
            "\n".join(lines)[:1900] if lines else "Заказов пока нет.", ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none())

    @store.command(name="status", description="Изменить состояние заказа (только продавец)")
    async def status_command(self, interaction: discord.Interaction, number: int,
                             action: Literal["confirm_payment", "complete", "cancel"]) -> None:
        await self.staff_action(interaction, number, action)


async def setup(bot):
    await bot.add_cog(Store(bot), guild=discord.Object(id=bot.config.guild_id))
