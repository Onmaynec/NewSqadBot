"""Состав функциональных модулей по утверждённому ТЗ."""
from __future__ import annotations

from dataclasses import dataclass
from importlib.util import find_spec


@dataclass(frozen=True, slots=True)
class ModuleInfo:
    key: str
    title: str


MODULES: tuple[ModuleInfo, ...] = (
    ModuleInfo("moderation", "Модерация"),
    ModuleInfo("welcome", "Приветствия"),
    ModuleInfo("roles", "Роли"),
    ModuleInfo("tickets", "Тикеты"),
    ModuleInfo("audit_logs", "Аудит"),
    ModuleInfo("leveling", "Уровни"),
    ModuleInfo("economy", "Экономика"),
    ModuleInfo("store", "Магазин заказов"),
    ModuleInfo("voice", "Голосовые комнаты"),
    ModuleInfo("events", "Мероприятия"),
    ModuleInfo("statistics", "Статистика"),
    ModuleInfo("achievements", "Достижения"),
    ModuleInfo("quests", "Задания"),
    ModuleInfo("profiles", "Профили"),
    ModuleInfo("suggestions", "Предложения"),
    ModuleInfo("party_finder", "Поиск компании"),
    ModuleInfo("membership", "Заявки на вступление"),
)


def implemented(key: str) -> bool:
    return any(module.key == key for module in MODULES) and find_spec(f"app.modules.{key}") is not None
