"""Discord storefront: persistent catalog panel and per-order buyer/staff controls."""
from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from app.services import store as records
from app.ui.theme import BRAND_BLUE

if TYPE_CHECKING:
    from app.modules.store import Store


STATE_LABELS = {
    "awaiting_payment": "Ожидает оплаты",
    "payment_reported": "Покупатель сообщил об оплате",
    "processing": "Оплата проверена · выполняется",
    "completed": "Выполнен",
    "cancelled": "Отменён",
}


def order_embed(order, *, staff: bool = False) -> discord.Embed:
    embed = discord.Embed(
        title=f"NEW SQAD SHOP / Заказ #{order.id:05d}",
        description=f"**{discord.utils.escape_markdown(order.product_title)}**\n"
                    f"Артикул: `{order.sku}` · Количество: {order.quantity}",
        color=BRAND_BLUE,
    )
    embed.add_field(name="Сумма", value=records.format_money(order.price_minor, order.currency))
    embed.add_field(name="Статус", value=STATE_LABELS.get(order.status, order.status))
    if staff:
        embed.add_field(name="Покупатель", value=f"<@{order.user_id}>", inline=False)
    embed.set_footer(text="Оплата проверяется продавцом вручную · никаких паролей и токенов")
    return embed


def product_embed(item) -> discord.Embed:
    embed = discord.Embed(
        title=discord.utils.escape_markdown(item.title),
        description=discord.utils.escape_mentions(item.description) or "Цифровой товар или услуга.",
        color=BRAND_BLUE,
    )
    embed.add_field(name="Цена за единицу", value=records.format_money(item.price_minor, item.currency))
    embed.add_field(name="Наличие", value="Под заказ" if item.stock is None else str(item.stock))
    embed.set_footer(text=f"Артикул: {item.sku} · Перед заказом проверьте условия")
    return embed


class StorePanelView(discord.ui.View):
    def __init__(self, cog: Store):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Открыть каталог", style=discord.ButtonStyle.primary,
                       custom_id="newsqad:store:catalog")
    async def catalog(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await self.cog.customer(interaction):
            return
        entries = await records.products(self.cog.bot.database, interaction.guild_id, public=True)
        if not entries:
            await interaction.response.send_message("Магазин пока пуст.", ephemeral=True)
            return
        await interaction.response.send_message(
            embed=discord.Embed(title="NEW SQAD SHOP / Каталог",
                description="Выберите товар ниже. Все суммы фиксируются при оформлении. "
                            "Никаких автоматических списаний нет.", color=BRAND_BLUE),
            view=StoreCatalogView(self.cog, interaction.user.id, entries), ephemeral=True)

    @discord.ui.button(label="Мои заказы", style=discord.ButtonStyle.secondary,
                       custom_id="newsqad:store:mine")
    async def mine(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if await self.cog.customer(interaction):
            await self.cog.show_my_orders(interaction)


class StoreProductSelect(discord.ui.Select):
    def __init__(self, view_cog: Store, user_id: int, entries: list):
        self.cog, self.user_id = view_cog, user_id
        super().__init__(placeholder="Выберите товар", min_values=1, max_values=1,
            options=[discord.SelectOption(
                label=entry.title[:95],
                description=(records.format_money(entry.price_minor, entry.currency)
                             + " · " + entry.sku)[:100],
                value=str(entry.id)) for entry in entries])

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Этот каталог открыт другим участником.", ephemeral=True)
            return
        # Modal must be the first response to this interaction.
        await interaction.response.send_modal(StoreQuantityModal(self.cog, self.user_id, int(self.values[0])))


class StoreCatalogView(discord.ui.View):
    def __init__(self, cog: Store, user_id: int, entries: list):
        super().__init__(timeout=300)
        self.cog, self.user_id, self.entries, self.page = cog, user_id, entries, 0
        self.draw()

    def draw(self) -> None:
        self.clear_items()
        start = self.page * 25
        self.add_item(StoreProductSelect(self.cog, self.user_id, self.entries[start:start + 25]))
        self.previous.disabled = self.page == 0
        self.following.disabled = start + 25 >= len(self.entries)
        self.add_item(self.previous)
        self.add_item(self.following)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Это чужой каталог.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Назад", style=discord.ButtonStyle.secondary, row=1)
    async def previous(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.page = max(0, self.page - 1)
        self.draw()
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="Далее", style=discord.ButtonStyle.secondary, row=1)
    async def following(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.page = min((len(self.entries) - 1) // 25, self.page + 1)
        self.draw()
        await interaction.response.edit_message(view=self)


class StoreQuantityModal(discord.ui.Modal, title="Оформление заказа"):
    quantity = discord.ui.TextInput(
        label="Количество (1–10)", default="1", min_length=1, max_length=2,
        placeholder="1")

    def __init__(self, cog: Store, user_id: int, product_id: int):
        super().__init__(timeout=180)
        self.cog, self.user_id, self.product_id = cog, user_id, product_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.user_id or interaction.guild_id != self.cog.bot.config.guild_id:
            await interaction.response.send_message("Недоступный заказ.", ephemeral=True)
            return
        value = str(self.quantity.value).strip()
        if not value.isascii() or not value.isdecimal() or not 1 <= int(value) <= 10:
            await interaction.response.send_message("Введите целое количество от 1 до 10.", ephemeral=True)
            return
        item = await records.product_by_id(self.cog.bot.database, interaction.guild_id, self.product_id)
        if item is None or not item.active or item.stock == 0 or (
                item.stock is not None and item.stock < int(value)):
            await interaction.response.send_message("Товар закончился или стал недоступен.", ephemeral=True)
            return
        total = item.price_minor * int(value)
        embed = product_embed(item)
        embed.title = "Подтверждение заказа"
        embed.add_field(name="Количество", value=value)
        embed.add_field(name="Итого", value=records.format_money(total, item.currency))
        embed.add_field(name="Важно", value=(
            "Это заявка на покупку. Оплата выполняется отдельно и подтверждается "
            "продавцом. Не присылайте пароли, токены или данные банковских карт."), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True,
            view=StoreCheckoutView(self.cog, self.user_id, item.id, int(value), item.price_minor))


class StoreCheckoutView(discord.ui.View):
    def __init__(self, cog: Store, user_id: int, product_id: int,
                 quantity: int, expected_price: int):
        super().__init__(timeout=300)
        self.cog, self.user_id, self.product_id = cog, user_id, product_id
        self.quantity, self.expected_price = quantity, expected_price

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Это чужой заказ.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Подтвердить заказ", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await self.cog.customer(interaction):
            return
        self.stop()
        for component in self.children:
            component.disabled = True
        await interaction.response.defer(ephemeral=True, thinking=True)
        if not await self.cog.ready_for_orders(interaction.guild):
            await interaction.followup.send(
                "Продавец пока не настроил приватный канал заказов. Покупка недоступна.",
                ephemeral=True)
            return
        try:
            order, created = await records.make_order(
                self.cog.bot.database, interaction.guild_id, self.user_id,
                self.product_id, interaction.id,
                quantity=self.quantity, expected_price=self.expected_price)
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        if created:
            await self.cog.notify_new(order)
        instructions = await self.cog.payment_instructions(interaction.guild_id)
        await interaction.followup.send(
            embed=order_embed(order), ephemeral=True,
            content=(f"**Заказ #{order.id:05d} зарегистрирован.**\n"
                     f"{discord.utils.escape_mentions(instructions)}\n"
                     "После оплаты нажмите «Я оплатил». Это не подтверждает получение денег."),
            view=StoreBuyerOrderView(self.cog, self.user_id, order.id))

    @discord.ui.button(label="Отмена", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.stop()
        await interaction.response.edit_message(content="Оформление отменено.", embed=None, view=None)


class StoreBuyerOrderView(discord.ui.View):
    def __init__(self, cog: Store, user_id: int, order_id: int):
        super().__init__(timeout=900)
        self.cog, self.user_id, self.order_id = cog, user_id, order_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Это чужой заказ.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Я оплатил", style=discord.ButtonStyle.primary)
    async def reported(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.cog.buyer_action(interaction, self.order_id, "report_payment")

    @discord.ui.button(label="Отменить заказ", style=discord.ButtonStyle.danger)
    async def cancelled(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.cog.buyer_action(interaction, self.order_id, "cancel")


class StoreStaffOrderView(discord.ui.View):
    def __init__(self, cog: Store, order_id: int):
        super().__init__(timeout=None)
        self.cog, self.order_id = cog, order_id
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.custom_id = f"newsqad:store:{item.custom_id}:{order_id}"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not await self.cog.staff(interaction):
            await interaction.response.send_message(
                "Только администратор магазина может обработать заказ.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Оплата проверена", style=discord.ButtonStyle.success,
                       custom_id="confirm_payment")
    async def paid(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.cog.staff_action(interaction, self.order_id, "confirm_payment")

    @discord.ui.button(label="Выполнен", style=discord.ButtonStyle.primary, custom_id="complete")
    async def finished(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.cog.staff_action(interaction, self.order_id, "complete")

    @discord.ui.button(label="Отменить", style=discord.ButtonStyle.danger, custom_id="cancel")
    async def cancelled(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.cog.staff_action(interaction, self.order_id, "cancel")
