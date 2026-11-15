"""Единый формат сроков наказаний: 30m, 2h, 10d, 2w, per."""
import re

_DURATION = re.compile(r"^([1-9][0-9]{0,5})([mhdw])$")
_SCALE = {"m": 60, "h": 3600, "d": 86400, "w": 604800}


def parse_duration(value: str, *, allow_permanent: bool = True, max_seconds: int | None = None) -> int | None:
    if value == "per" and allow_permanent:
        return None
    match = _DURATION.fullmatch(value)
    if not match:
        raise ValueError("Укажите срок: 30m, 2h, 10d, 2w или per.")
    seconds = int(match[1]) * _SCALE[match[2]]
    if max_seconds is not None and seconds > max_seconds:
        raise ValueError(f"Максимальная длительность: {max_seconds // 86400} дн.")
    return seconds


def nickname(username: str, chosen_name: str) -> str:
    """Сохраняет имя и разделитель; сокращает только Discord username."""
    name = chosen_name.strip()
    if not name or "\n" in name or "\r" in name or "@" in name or "`" in name:
        raise ValueError("Имя должно быть без упоминаний и служебных символов.")
    available = 32 - len(name) - 3
    if available < 1:
        raise ValueError("Имя слишком длинное для серверного ника.")
    return f"{username[:available]} | {name}"
