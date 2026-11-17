"""Управление черновиками, расписанием и опубликованными объявлениями."""
from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands

from app.config import VERSION
from app.core.permissions import can_manage_settings
from app.modules.announcements import build_announcement
from app.services import announcements as store
from app.services.audit import log_event
from app.ui.announcement_controls import (
    EditAnnouncementModal,
    display_preview,
)
from app.ui.theme import BRAND_BLUE, NO_MENTIONS, card, notice

log = logging.getLogger(__name__)


class AnnouncementManager(commands.Cog):
    announcements = app_commands.Group(
        name="announcements", description="Черновики, расписание и история объявлений",
    )

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.scheduler.register("announcements.publish", self._scheduled)
        count = await store.recover_sending(self.bot.database, self.bot.config.guild_id)
        if count:
            log.warning("Публикации после перезапуска требуют проверки count=%s", count)
        for row in await store.list_items(
            self.bot.database, self.bot.config.guild_id,
            statuses=("scheduled",), limit=10000,
        ):
            await self._enqueue(row)

    async def _enqueue(self, row) -> None:
        await self.bot.scheduler.schedule(
            job_key=f"announcement-{row.guild_id}-{row.id}",
            handler="announcements.publish", due_at=row.due_at,
            payload={"guild_id": row.guild_id, "id": row.id},
        )

    def admin(self, interaction: discord.Interaction) -> bool:
        return can_manage_settings(interaction, self.bot.config.owner_ids)

    async def _scheduled(self, payload: dict) -> None:
        if int(payload["guild_id"]) != self.bot.config.guild_id:
            return
        await self.bot.wait_until_ready()
        row = await store.get(self.bot.database, self.bot.config.guild_id, int(payload["id"]))
        if row and row.status == "scheduled":
            await self._deliver(row, scheduled_only=True)

    async def preview_draft(self, interaction: discord.Interaction, *,
                            channel_id: int, kind: str, color: int,
                            title: str, body: str) -> None:
        if not self.admin(interaction):
            await interaction.response.send_message(
                embed=notice("Недостаточно прав на объявления.", kind="warning"), ephemeral=True,
            )
            return
        try:
            build_announcement(kind, title, body, color)
            row = await store.create(
                self.bot.database, guild_id=interaction.guild_id,
                author_id=interaction.user.id, channel_id=channel_id,
                kind=kind, title=title, body=body, color=color,
            )
        except ValueError as exc:
            await interaction.response.send_message(embed=notice(str(exc), kind="warning"), ephemeral=True)
            return
        await display_preview(interaction, self, row)

    async def _deliver(self, row, *, scheduled_only: bool) -> str:
        guild = self.bot.get_guild(row.guild_id)
        if guild is None:
            raise RuntimeError(f"Сервер недоступен announcement={row.id}")
        if scheduled_only:
            try:
                actor = guild.get_member(row.author_id) or await guild.fetch_member(row.author_id)
            except discord.NotFound:
                actor = None
            ctx = SimpleNamespace(guild=guild, guild_id=guild.id, user=actor)
            if actor is None or not self.admin(ctx):
                claimed = await store.claim(
                    self.bot.database, guild_id=guild.id, number=row.id, scheduled_only=True,
                )
                if claimed:
                    await store.mark_error(
                        self.bot.database, guild_id=guild.id, number=row.id,
                        uncertain=False, detail="Автор потерял административные права.",
                    )
                return "Автор объявления больше не имеет прав администратора."

        claimed = await store.claim(
            self.bot.database, guild_id=guild.id, number=row.id,
            scheduled_only=scheduled_only,
        )
        if claimed is None:
            return "Объявление отменено, уже отправлено или больше не ожидает отправки."
        channel = guild.get_channel(claimed.channel_id)
        if not isinstance(channel, discord.TextChannel):
            await store.mark_error(
                self.bot.database, guild_id=guild.id, number=row.id,
                uncertain=False, detail="Канал публикации отсутствует.",
            )
            return "Канал объявления не найден."
        bot_member = guild.me
        perms = channel.permissions_for(bot_member) if bot_member else None
        if (not perms or not perms.view_channel or not perms.send_messages
                or (claimed.kind == "embed" and not perms.embed_links)):
            await store.mark_error(
                self.bot.database, guild_id=guild.id, number=row.id,
                uncertain=False, detail="Недостаточно разрешений на отправку.",
            )
            return "Боту не хватает разрешений в выбранном канале."
        try:
            message = await channel.send(**build_announcement(
                claimed.kind, claimed.title, claimed.body, claimed.color,
            ))
        except discord.Forbidden:
            await store.mark_error(
                self.bot.database, guild_id=guild.id, number=row.id,
                uncertain=False, detail="Discord 403 Forbidden.",
            )
            return "Discord запретил отправку. Проверьте права бота."
        except discord.HTTPException as exc:
            await store.mark_error(
                self.bot.database, guild_id=guild.id, number=row.id,
                uncertain=True, detail=f"Discord HTTP {getattr(exc, 'status', 0)}; результат неизвестен.",
            )
            return "Результат не подтверждён. Проверьте канал перед новой публикацией."
        # Сообщение уже отправлено. При сбое SQLite отправку повторять нельзя.
        await store.mark_sent(
            self.bot.database, guild_id=guild.id, number=row.id, message_id=message.id,
        )
        try:
            await log_event(
                self.bot, channel_setting="audit_channel_id",
                title="Объявление опубликовано",
                details=(f"Объявление #{row.id}; автор {row.author_id}; "
                         f"канал {row.channel_id}; сообщение {message.id}; "
                         f"отложенное {scheduled_only}"),
                actor_id=row.author_id, action="announcements.published",
                target_id=message.id,
            )
        except Exception:
            log.exception("Публикация успешна, аудит не записан announcement=%s", row.id)
        return f"Объявление #{row.id} опубликовано. {message.jump_url}"

    async def send_record(self, interaction: discord.Interaction, number: int) -> None:
        if not self.admin(interaction):
            await interaction.response.send_message(
                embed=notice("Только администрация.", kind="warning"), ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        row = await store.get(self.bot.database, interaction.guild_id, number)
        if row is None:
            result = "Объявление не найдено."
        elif row.status != "draft":
            result = "Можно отправить только черновик. Создайте копию через duplicate."
        else:
            try:
                result = await self._deliver(row, scheduled_only=False)
            except Exception:
                log.exception("Ошибка публикации announcement=%s", number)
                result = "Не удалось завершить отправку. Проверьте историю и канал."
        await interaction.followup.send(
            embed=notice(result), ephemeral=True, allowed_mentions=NO_MENTIONS,
        )

    async def cancel_record(self, interaction: discord.Interaction, number: int) -> None:
        if not self.admin(interaction):
            await interaction.response.send_message(embed=notice("Только администрация."), ephemeral=True)
            return
        try:
            await store.cancel(self.bot.database, guild_id=interaction.guild_id, number=number)
            text = f"Объявление #{number} отменено."
        except ValueError as exc:
            text = str(exc)
        await interaction.response.send_message(embed=notice(text), ephemeral=True)

    async def schedule_record(self, interaction: discord.Interaction,
                              number: int, value: str) -> None:
        if not self.admin(interaction):
            await interaction.response.send_message(embed=notice("Только администрация."), ephemeral=True)
            return
        try:
            due = store.parse_time(value, timezone_name=self.bot.config.timezone)
            row = await store.schedule(
                self.bot.database, guild_id=interaction.guild_id,
                number=number, due_at=due,
            )
            await self._enqueue(row)
        except ValueError as exc:
            await interaction.response.send_message(embed=notice(str(exc), kind="warning"), ephemeral=True)
            return
        except Exception:
            log.exception("Ошибка планировщика объявления id=%s", number)
            await interaction.response.send_message(embed=notice(
                "Расписание сохранено. Для восстановления очереди перезапустите бота.",
                kind="warning"), ephemeral=True)
            return
        await interaction.response.send_message(
            embed=notice(f"Объявление #{number} запланировано на <t:{due}:F>.", kind="success"),
            ephemeral=True,
        )

    async def edit_record(self, interaction: discord.Interaction, *,
                          number: int, title: str, body: str) -> None:
        if not self.admin(interaction):
            await interaction.response.send_message(embed=notice("Только администрация."), ephemeral=True)
            return
        row = await store.get(self.bot.database, interaction.guild_id, number)
        if row is None:
            await interaction.response.send_message(embed=notice("Объявление не найдено."), ephemeral=True)
            return
        try:
            payload = build_announcement(row.kind, title, body, row.color)
            if row.status == "sent":
                channel = interaction.guild.get_channel(row.channel_id)
                if not isinstance(channel, discord.TextChannel) or row.message_id is None:
                    raise ValueError("Опубликованное сообщение недоступно.")
                await interaction.response.defer(ephemeral=True, thinking=True)
                message = await channel.fetch_message(row.message_id)
                await message.edit(
                    content=payload.get("content"), embed=payload.get("embed"),
                    allowed_mentions=NO_MENTIONS,
                )
                await store.update_sent(
                    self.bot.database, guild_id=row.guild_id, number=row.id,
                    title=title, body=body,
                )
                await interaction.followup.send(
                    embed=notice(f"Объявление #{row.id} изменено.", kind="success"), ephemeral=True,
                )
                return
            changed = await store.edit(
                self.bot.database, guild_id=row.guild_id, number=row.id,
                actor_id=interaction.user.id, title=title, body=body,
            )
        except ValueError as exc:
            if interaction.response.is_done():
                await interaction.followup.send(embed=notice(str(exc), kind="warning"), ephemeral=True)
            else:
                await interaction.response.send_message(embed=notice(str(exc), kind="warning"), ephemeral=True)
            return
        except discord.HTTPException:
            log.exception("Не удалось отредактировать announcement=%s", number)
            await interaction.followup.send(
                embed=notice("Discord не разрешил изменить сообщение.", kind="error"), ephemeral=True,
            )
            return
        await display_preview(interaction, self, changed)

    async def _list(self, interaction: discord.Interaction,
                    statuses: tuple[str, ...], title: str) -> None:
        if not self.admin(interaction):
            await interaction.response.send_message(embed=notice("Только администрация."), ephemeral=True)
            return
        rows = await store.list_items(
            self.bot.database, interaction.guild_id, statuses=statuses,
        )
        lines = [
            f"**#{row.id}** · {discord.utils.escape_mentions(row.title[:70])} · "
            f"{store.STATUS_LABELS[row.status]}"
            + (f" · <t:{row.due_at}:F>" if row.status == "scheduled" and row.due_at else "")
            for row in rows
        ]
        await interaction.response.send_message(
            embed=card(f"NewSquad / Объявления / {title}",
                       "\n".join(lines) or "Записей нет."),
            ephemeral=True, allowed_mentions=NO_MENTIONS,
        )

    @announcements.command(name="drafts", description="Открыть список черновиков")
    async def drafts(self, interaction: discord.Interaction) -> None:
        await self._list(interaction, ("draft", "failed"), "Черновики")

    @announcements.command(name="scheduled", description="Посмотреть расписание публикаций")
    async def scheduled(self, interaction: discord.Interaction) -> None:
        await self._list(interaction, ("scheduled",), "Расписание")

    @announcements.command(name="history", description="История отправленных объявлений")
    async def history(self, interaction: discord.Interaction) -> None:
        await self._list(interaction, ("sent", "uncertain", "failed", "cancelled"), "История")

    @announcements.command(name="send", description="Отправить черновик по номеру")
    async def send_existing(self, interaction: discord.Interaction, number: int) -> None:
        await self.send_record(interaction, number)

    @announcements.command(name="edit", description="Изменить черновик или публикацию")
    async def edit_existing(self, interaction: discord.Interaction, number: int) -> None:
        if not self.admin(interaction):
            await interaction.response.send_message(embed=notice("Только администрация."), ephemeral=True)
            return
        row = await store.get(self.bot.database, interaction.guild_id, number)
        if row is None or row.status not in ("draft", "failed", "sent"):
            await interaction.response.send_message(
                embed=notice("Редактирование недоступно для этого статуса.", kind="warning"),
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(EditAnnouncementModal(self, row))

    @announcements.command(name="cancel", description="Отменить черновик или запланированную отправку")
    async def cancel_existing(self, interaction: discord.Interaction, number: int) -> None:
        await self.cancel_record(interaction, number)

    @announcements.command(name="duplicate", description="Создать новый черновик на основе старого")
    async def duplicate_existing(self, interaction: discord.Interaction, number: int) -> None:
        if not self.admin(interaction):
            await interaction.response.send_message(embed=notice("Только администрация."), ephemeral=True)
            return
        try:
            row = await store.duplicate(
                self.bot.database, guild_id=interaction.guild_id,
                number=number, author_id=interaction.user.id,
            )
        except ValueError as exc:
            await interaction.response.send_message(embed=notice(str(exc)), ephemeral=True)
            return
        await display_preview(interaction, self, row)

    @announcements.command(name="template-save", description="Сохранить объявление в шаблоны")
    async def template_save(self, interaction: discord.Interaction,
                            number: int, name: str) -> None:
        if not self.admin(interaction):
            await interaction.response.send_message(embed=notice("Только администрация."), ephemeral=True)
            return
        try:
            await store.save_template(
                self.bot.database, guild_id=interaction.guild_id,
                slug=name, author_id=interaction.user.id, number=number,
            )
        except ValueError as exc:
            await interaction.response.send_message(embed=notice(str(exc), kind="warning"), ephemeral=True)
            return
        await interaction.response.send_message(
            embed=notice("Шаблон сохранён: " + name, kind="success"), ephemeral=True,
        )

    @announcements.command(name="templates", description="Список шаблонов объявлений")
    async def templates_command(self, interaction: discord.Interaction) -> None:
        if not self.admin(interaction):
            await interaction.response.send_message(embed=notice("Только администрация."), ephemeral=True)
            return
        items = await store.templates(self.bot.database, interaction.guild_id)
        lines = [f"**{row.slug}** — {discord.utils.escape_mentions(row.title)}" for row in items]
        await interaction.response.send_message(embed=card(
            "NewSquad / Шаблоны", "\n".join(lines) or "Сохранённых шаблонов нет."),
            ephemeral=True,
        )

    @announcements.command(name="from-template", description="Создать объявление из шаблона")
    async def from_saved_template(self, interaction: discord.Interaction,
                                  name: str, channel: discord.TextChannel) -> None:
        if not self.admin(interaction) or channel.guild.id != interaction.guild_id:
            await interaction.response.send_message(embed=notice("Только администрация."), ephemeral=True)
            return
        try:
            row = await store.from_template(
                self.bot.database, guild_id=interaction.guild_id,
                slug=name, author_id=interaction.user.id, channel_id=channel.id,
            )
        except ValueError as exc:
            await interaction.response.send_message(embed=notice(str(exc), kind="warning"), ephemeral=True)
            return
        await display_preview(interaction, self, row)

    @announcements.command(name="preset", description="Создать объявление по готовому образцу")
    async def preset(self, interaction: discord.Interaction, channel: discord.TextChannel,
                     kind: Literal["release", "news", "event"]) -> None:
        if not self.admin(interaction) or channel.guild.id != interaction.guild_id:
            await interaction.response.send_message(embed=notice("Только администрация."), ephemeral=True)
            return
        presets = {
            "release": (f"NewSqadBot v{VERSION}", "**Изменения обновления**\n\n"
                        "- Основные функции\n- Исправления\n- Примечания"),
            "news": ("Новости NewSquad", "**Что нового**\n\n"
                     "- Главное\n- Подробности\n- Следующие планы"),
            "event": ("Мероприятие NewSquad", "**Подробности**\n\n"
                      "- Дата и время\n- Правила\n- Как участвовать"),
        }
        title, body = presets[kind]
        row = await store.create(
            self.bot.database, guild_id=interaction.guild_id,
            author_id=interaction.user.id, channel_id=channel.id,
            kind="embed", title=title, body=body, color=BRAND_BLUE,
        )
        await display_preview(interaction, self, row)


async def setup(bot) -> None:
    await bot.add_cog(AnnouncementManager(bot), guild=discord.Object(id=bot.config.guild_id))
