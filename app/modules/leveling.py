"""Уровни NewSquad: XP за общение и реальные совместные голосовые минуты."""
from __future__ import annotations

import asyncio
import logging
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from app.core.permissions import can_manage_settings
from app.services import progression as progress
from app.services.role_rules import is_approved
from app.ui.theme import card, notice

log = logging.getLogger(__name__)


class Leveling(commands.Cog):
    levels = app_commands.Group(name="levels", description="Активность и уровни NewSquad")

    def __init__(self, bot):
        self.bot = bot
        self.bot.events.subscribe("leveling.level_up", "leveling.announce", self.announce)

    async def cog_load(self) -> None:
        self.voice_tick.start()
        log.info("Уровни запущены: текстовый XP требует ENABLE_MESSAGE_CONTENT_INTENT=true")

    async def cog_unload(self) -> None:
        self.voice_tick.cancel()

    async def approved(self, member: discord.Member) -> bool:
        if member.guild.id != self.bot.config.guild_id or member.bot or member.pending:
            return False
        role_id = await self.bot.database.get_setting(member.guild.id, "member_role_id")
        return is_approved(member, role_id)

    async def _allowed(self, guild_id: int, kind: str) -> bool:
        return bool(await self.bot.database.get_setting(guild_id, "leveling_enabled", True)
                    and await self.bot.database.get_setting(guild_id, f"leveling_{kind}_enabled", True))

    async def _settings(self, guild_id: int) -> dict:
        names = {
            "message_xp": 20, "cooldown_seconds": 60, "daily_cap": 1200,
            "voice_xp": 30, "excluded_channel_ids": [], "announce_channel_id": None,
        }
        return {key: await self.bot.database.get_setting(guild_id, f"leveling_{key}", val)
                for key, val in names.items()}

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if not self.bot.config.message_content_intent or not message.guild:
            return
        if message.guild.id != self.bot.config.guild_id or not isinstance(message.author, discord.Member):
            return
        if not await self.approved(message.author) or not await self._allowed(message.guild.id, "text"):
            return
        if message.webhook_id or message.type != discord.MessageType.default:
            return
        settings = await self._settings(message.guild.id)
        if message.channel.id in settings["excluded_channel_ids"]:
            return
        try:
            result = await progress.award_message(
                self.bot.database, guild_id=message.guild.id, user_id=message.author.id,
                message_id=message.id, content=message.content, timezone=self.bot.config.timezone,
                amount=int(settings["message_xp"]), cooldown=int(settings["cooldown_seconds"]),
                daily_cap=int(settings["daily_cap"]),
            )
            if result.amount and result.level_after != result.level_before:
                log.info("Новый уровень guild=%s user=%s level=%s source=message",
                         message.guild.id, message.author.id, result.level_after)
        except Exception:
            log.exception("Начисление текстового XP не удалось guild=%s user=%s message=%s",
                          message.guild.id, message.author.id, message.id)

    @tasks.loop(minutes=1)
    async def voice_tick(self) -> None:
        guild = self.bot.get_guild(self.bot.config.guild_id)
        if guild is None or not await self._allowed(guild.id, "voice"):
            return
        settings = await self._settings(guild.id)
        minute = int(time.time()) // 60
        role_id = await self.bot.database.get_setting(guild.id, "member_role_id")
        for channel in guild.voice_channels:
            if channel == guild.afk_channel or channel.id in settings["excluded_channel_ids"]:
                continue
            eligible = [m for m in channel.members if not m.bot and not m.pending
                        and m.voice is not None and m.voice.channel == channel
                        and not m.voice.deaf and not m.voice.self_deaf
                        and is_approved(m, role_id)]
            if len(eligible) < 2:
                continue
            for member in eligible:
                try:
                    result = await progress.record_voice_minute(
                        self.bot.database, guild_id=guild.id, user_id=member.id,
                        minute=minute, timezone=self.bot.config.timezone,
                        reward_xp=int(settings["voice_xp"]), daily_cap=int(settings["daily_cap"]),
                    )
                    if result.amount:
                        log.debug("Голосовой XP guild=%s user=%s +%s", guild.id, member.id, result.amount)
                except Exception:
                    log.exception("Ошибка голосового XP guild=%s user=%s minute=%s",
                                  guild.id, member.id, minute)

    @voice_tick.before_loop
    async def before_voice_tick(self) -> None:
        await self.bot.wait_until_ready()
        # Не награждаем сразу после запуска, пока реальная минута не прошла.
        await asyncio.sleep(60)

    async def announce(self, payload: dict) -> None:
        guild_id = int(payload["guild_id"])
        if guild_id != self.bot.config.guild_id:
            return
        target = await self.bot.database.get_setting(guild_id, "leveling_announce_channel_id")
        if not target:
            return
        guild = self.bot.get_guild(guild_id)
        channel = guild.get_channel(target) if guild else None
        if not isinstance(channel, discord.TextChannel):
            log.warning("Канал повышения уровней не найден guild=%s channel=%s", guild_id, target)
            return
        user_id, level = int(payload["user_id"]), int(payload["level"])
        member = guild.get_member(user_id)
        if member is None:
            return
        await channel.send(embed=card("Повышение уровня",
            f"<@{user_id}> достиг **{level}-го уровня** NewSquad!\n"
            "Открытые награды выдаются автоматически."),
            allowed_mentions=discord.AllowedMentions(users=False, roles=False, everyone=False))

    @app_commands.command(name="rank", description="Посмотреть уровень и опыт участника")
    async def rank(self, interaction: discord.Interaction, user: discord.Member | None = None) -> None:
        member = user or interaction.user
        if not isinstance(member, discord.Member) or not await self.approved(member):
            await interaction.response.send_message(embed=notice("Профиль уровня доступен только участникам NewSquad.",
                                                                  kind="warning"), ephemeral=True)
            return
        row = await progress.get_progress(self.bot.database, interaction.guild_id, member.id)
        xp = row.xp if row else 0
        level = progress.level_for_xp(xp)
        following = progress.threshold(level + 1)
        emb = card(f"Прогресс  /  {discord.utils.escape_markdown(member.display_name)}",
            f"**Уровень {level}**  ·  {xp:,} XP\n"
            f"{progress.progress_bar(xp)}\nДо {level+1}-го уровня: **{following-xp:,} XP**")
        emb.set_thumbnail(url=member.display_avatar.url)
        emb.add_field(name="Сообщений с XP", value=str(row.credited_messages if row else 0))
        emb.add_field(name="Активных голосовых минут", value=str(row.active_voice_minutes if row else 0))
        await interaction.response.send_message(embed=emb, ephemeral=False,
                                                allowed_mentions=discord.AllowedMentions.none())

    @levels.command(name="top", description="Рейтинг участников по опыту")
    async def top(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        rows = await progress.top(self.bot.database, self.bot.config.guild_id, limit=25)
        member_role = await self.bot.database.get_setting(self.bot.config.guild_id, "member_role_id")
        top = [(r, guild.get_member(r.user_id)) for r in rows]
        top = [(r, m) for r, m in top if m is not None and is_approved(m, member_role)][:10]
        description = "\n".join(
            f"**{index}.** {discord.utils.escape_markdown(member.display_name)}  ·  "
            f"**{r.level} ур.**  ·  {r.xp:,} XP"
            for index, (r, member) in enumerate(top, 1)
        ) or "Рейтинг пока пуст."
        await interaction.response.send_message(embed=card("NewSquad  /  Рейтинг уровней", description))

    async def admin(self, interaction: discord.Interaction) -> bool:
        if can_manage_settings(interaction, self.bot.config.owner_ids):
            return True
        await interaction.response.send_message(embed=notice("Настройка доступна только администрации.", kind="error"),
                                                ephemeral=True)
        return False

    @levels.command(name="settings", description="Текущие правила начисления опыта")
    async def settings(self, interaction: discord.Interaction) -> None:
        if not await self.admin(interaction):
            return
        config = await self._settings(interaction.guild_id)
        enabled = await self.bot.database.get_setting(interaction.guild_id, "leveling_enabled", True)
        txt = await self.bot.database.get_setting(interaction.guild_id, "leveling_text_enabled", True)
        voice = await self.bot.database.get_setting(interaction.guild_id, "leveling_voice_enabled", True)
        embed = card("NewSquad  /  Правила XP",
            f"**Уровни:** {'включены' if enabled else 'выключены'}\n"
            f"**Текст:** {'включён' if txt else 'выключен'} · "
            f"{'Message Content доступен' if self.bot.config.message_content_intent else 'нужно включить Message Content Intent'}\n"
            f"**Голосовые:** {'включены' if voice else 'выключены'}\n"
            f"**За сообщение:** {config['message_xp']} XP / {config['cooldown_seconds']} сек\n"
            f"**За 10 голосовых минут:** {config['voice_xp']} XP\n"
            f"**Лимит XP в сутки:** {config['daily_cap']}\n"
            f"**Исключённые каналы:** {len(config['excluded_channel_ids'])}\n"
            f"**Канал повышения уровней:** "
            f"{f'<#{config["announce_channel_id"]}>' if config['announce_channel_id'] else 'не выбран'}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @levels.command(name="switch", description="Включить/отключить общий, текстовый или голосовой XP")
    @app_commands.choices(source=[
        app_commands.Choice(name="Весь прогресс", value="all"),
        app_commands.Choice(name="Текстовые сообщения", value="text"),
        app_commands.Choice(name="Голосовые каналы", value="voice"),
    ])
    async def switch(self, interaction: discord.Interaction, source: app_commands.Choice[str], enabled: bool) -> None:
        if not await self.admin(interaction):
            return
        key = "leveling_enabled" if source.value == "all" else f"leveling_{source.value}_enabled"
        await self.bot.database.set_setting(interaction.guild_id, key, enabled, actor_id=interaction.user.id)
        await interaction.response.send_message(embed=notice("Настройка сохранена.", kind="success"), ephemeral=True)

    @levels.command(name="rates", description="Настроить XP, задержку и дневной лимит")
    async def rates(self, interaction: discord.Interaction,
                    message_xp: app_commands.Range[int, 1, 100],
                    voice_xp: app_commands.Range[int, 1, 200],
                    cooldown_seconds: app_commands.Range[int, 0, 3600],
                    daily_cap: app_commands.Range[int, 1, 20000]) -> None:
        if not await self.admin(interaction):
            return
        for key, value in {"message_xp": message_xp, "voice_xp": voice_xp,
                           "cooldown_seconds": cooldown_seconds, "daily_cap": daily_cap}.items():
            await self.bot.database.set_setting(interaction.guild_id, f"leveling_{key}", value,
                                                actor_id=interaction.user.id)
        await interaction.response.send_message(embed=notice("Параметры XP обновлены.", kind="success"), ephemeral=True)

    @levels.command(name="exclude", description="Исключить текстовый или голосовой канал из начислений")
    async def exclude(self, interaction: discord.Interaction, channel: discord.TextChannel | discord.VoiceChannel) -> None:
        await self._channel_setting(interaction, channel, True)

    @levels.command(name="include", description="Вернуть начисления XP в канале")
    async def include(self, interaction: discord.Interaction, channel: discord.TextChannel | discord.VoiceChannel) -> None:
        await self._channel_setting(interaction, channel, False)

    async def _channel_setting(self, interaction: discord.Interaction,
                               channel: discord.TextChannel | discord.VoiceChannel, exclude: bool) -> None:
        if not await self.admin(interaction):
            return
        if channel.guild.id != self.bot.config.guild_id:
            await interaction.response.send_message("Канал не относится к NewSquad.", ephemeral=True)
            return
        key = "leveling_excluded_channel_ids"
        ids = set(await self.bot.database.get_setting(interaction.guild_id, key, []))
        if exclude:
            ids.add(channel.id)
        else:
            ids.discard(channel.id)
        await self.bot.database.set_setting(interaction.guild_id, key, sorted(ids), actor_id=interaction.user.id)
        await interaction.response.send_message(embed=notice("Исключения каналов обновлены.", kind="success"),
                                                ephemeral=True)

    @levels.command(name="announce", description="Выбрать канал сообщений о повышении уровня")
    async def announce_channel(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        if not await self.admin(interaction):
            return
        if channel.guild.id != self.bot.config.guild_id:
            await interaction.response.send_message("Неверный сервер.", ephemeral=True)
            return
        await self.bot.database.set_setting(interaction.guild_id, "leveling_announce_channel_id", channel.id,
                                            actor_id=interaction.user.id)
        await interaction.response.send_message(embed=notice("Канал повышений настроен.", kind="success"),
                                                ephemeral=True)


async def setup(bot) -> None:
    await bot.add_cog(Leveling(bot), guild=discord.Object(id=bot.config.guild_id))
