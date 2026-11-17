"""Белый список настроек для локальной панели (нет доступа к токенам/.env)."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from app.config import FEMALE_ROLE_ID
from app.core.catalog import MODULES
from app.panels.botsettings import CHANNEL_SLOTS, ROLE_SLOTS


@dataclass(frozen=True, slots=True)
class Setting:
    key: str
    label: str
    group: str
    kind: str
    default: Any = None
    description: str = ""
    minimum: int = 0
    maximum: int = 100_000
    channel_kind: str = "text"
    choices: tuple[tuple[str, str], ...] = ()
    restart_required: bool = False

    def public(self) -> dict[str, Any]:
        return asdict(self)


def flag(key: str, label: str, group: str, default: bool = True,
         description: str = "") -> Setting:
    return Setting(key, label, group, "bool", default, description)


def count(key: str, label: str, group: str, default: int, minimum: int,
          maximum: int, description: str = "") -> Setting:
    return Setting(key, label, group, "int", default, description, minimum, maximum)


ROLE_SETTINGS = tuple(Setting(key, label, "Роли", "role",
                              FEMALE_ROLE_ID if key == "female_role_id" else None,
                              "Роль должна существовать на сервере NewSquad.")
                      for key, label in ROLE_SLOTS)

CHANNEL_SETTINGS = tuple(Setting(key, label, "Каналы", "channel", None,
                                 "Выберите существующий Discord-канал.",
                                 channel_kind=("category" if key.endswith("category_channel_id")
                                               else "voice" if key == "voice_lobby_channel_id"
                                               else "text"))
                         for key, label in CHANNEL_SLOTS)

EXTRA_SETTINGS: tuple[Setting, ...] = (
    Setting("party_channel_id", "Канал поиска команды", "Каналы", "channel"),
    Setting("leveling_announce_channel_id", "Канал объявлений о уровнях", "Каналы", "channel"),

    flag("welcome_enabled", "Приветствия новых участников", "Участники"),
    flag("farewell_enabled", "Сообщения о выходе", "Участники"),
    count("membership_min_age", "Минимальный возраст заявки", "Участники", 13, 1, 120),
    Setting("membership_nickname_template", "Формат серверного ника", "Участники", "choice",
            "default", "Применяется только к новым одобрениям.", choices=(
                ("default", "DiscordNick | Имя"), ("name_first", "Имя | DiscordNick"),
                ("name_only", "Только имя"), ("username_only", "Только DiscordNick"))),

    flag("tickets_enabled", "Принимать обращения", "Поддержка"),
    count("tickets_max_open", "Тикетов на участника", "Поддержка", 3, 1, 10),
    count("tickets_inactivity_days", "Автозакрытие, дней (0 — выкл.)", "Поддержка", 0, 0, 30),
    count("tickets_reply_alert_hours", "Предупреждение без ответа, часов", "Поддержка", 6, 0, 168,
          "Только сведения о факте отсутствия ответа; переписка не сохраняется."),

    flag("voice_enabled", "Временные голосовые комнаты", "Голосовые"),
    count("voice_max_rooms", "Максимум комнат", "Голосовые", 30, 1, 100),
    count("voice_default_limit", "Лимит участников (0 — без лимита)", "Голосовые", 0, 0, 99),

    flag("leveling_enabled", "XP и уровни", "Прогрессия"),
    flag("leveling_text_enabled", "Опыт за сообщения", "Прогрессия"),
    flag("leveling_voice_enabled", "Опыт за голосовую активность", "Прогрессия"),
    count("leveling_message_xp", "XP за сообщение", "Прогрессия", 20, 0, 10000),
    count("leveling_voice_xp", "XP за интервал голоса", "Прогрессия", 30, 0, 10000),
    count("leveling_cooldown_seconds", "Cooldown за сообщения, сек.", "Прогрессия", 60, 0, 3600),
    count("leveling_daily_cap", "Суточный лимит XP", "Прогрессия", 1200, 0, 100000),
    Setting("leveling_excluded_channel_ids", "Без XP: исключённые каналы",
            "Прогрессия", "channel_list", [],
            "Можно выбрать несколько каналов; сохраняется список Discord ID."),

    flag("economy_enabled", "NS Coins", "Экономика"),
    flag("economy_activity_enabled", "Награды за активность", "Экономика"),
    flag("economy_transfers_enabled", "Переводы между участниками", "Экономика"),
    count("economy_daily_reward", "Ежедневная награда", "Экономика", 100, 0, 100000),
    count("economy_message_reward", "Монеты за сообщения", "Экономика", 2, 0, 10000),
    count("economy_voice_reward", "Монеты за голосовой XP", "Экономика", 5, 0, 10000),
    count("economy_level_reward", "Монеты за уровень", "Экономика", 25, 0, 100000),

    flag("quests_enabled", "Задания", "Задания"),
    flag("quests_daily_enabled", "Ежедневные задания", "Задания"),
    flag("quests_weekly_enabled", "Еженедельные задания", "Задания"),
    flag("achievements_enabled", "Достижения", "Задания"),

    flag("events_enabled", "Мероприятия", "Сообщество"),
    flag("giveaways_enabled", "Розыгрыши", "Сообщество"),
    flag("party_enabled", "Party Finder", "Сообщество"),
    flag("suggestions_enabled", "Предложения участников", "Сообщество"),
    count("suggest_min_level", "Уровень для подачи идеи", "Сообщество", 1, 0, 1000),
    count("suggest_cooldown_seconds", "Интервал между идеями, сек.", "Сообщество", 1800, 0, 86400),

    flag("statistics_enabled", "Статистика сервера", "Статистика"),
    flag("statistics_messages_enabled", "Учёт количества сообщений", "Статистика"),
    flag("statistics_voice_enabled", "Учёт голосовой активности", "Статистика"),
    flag("statistics_public_ranking", "Публичные рейтинги", "Статистика"),
    Setting("statistics_timezone", "Часовой пояс статистики", "Статистика", "timezone",
            "Europe/Tallinn", "Название IANA: Europe/Tallinn, Europe/Helsinki или UTC."),
)

MODULE_SETTINGS = tuple(Setting(
    f"module.{module.key}.enabled", module.title, "Модули", "bool", True,
    "Изменение вступит в силу после перезапуска контейнера.",
    restart_required=True,
) for module in MODULES)

SETTINGS = (*ROLE_SETTINGS, *CHANNEL_SETTINGS, *EXTRA_SETTINGS, *MODULE_SETTINGS)
BY_KEY = {item.key: item for item in SETTINGS}
assert len(BY_KEY) == len(SETTINGS), "Duplicate settings must not silently overwrite each other"

GROUPS = (
    "Роли", "Каналы", "Участники", "Поддержка", "Голосовые",
    "Прогрессия", "Экономика", "Задания", "Сообщество", "Статистика", "Модули",
)
