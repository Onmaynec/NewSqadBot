"""Documented module lifecycle contracts; additive metadata, not a new loader.

Dependencies are advisory unless marked hard, so established Discord commands
keep running when an optional neighboring cog is disabled.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.core.catalog import MODULES


@dataclass(frozen=True, slots=True)
class RuntimeContract:
    key: str
    title: str
    hard_dependencies: tuple[str, ...]
    settings_prefix: str
    requires_gateway: bool
    restart_policy: str
    failure_policy: str


SOFT_DEPENDENCIES = {
    "voice": ("membership",),
    "store": (),
    "tickets": ("membership",),
    "welcome": ("membership",),
    "roles": ("membership",),
    "achievements": ("leveling",),
    "quests": ("leveling",),
    "profiles": ("leveling",),
}


def contracts() -> tuple[RuntimeContract, ...]:
    return tuple(RuntimeContract(
        key=module.key, title=module.title,
        hard_dependencies=(),
        settings_prefix=f"module.{module.key}.",
        requires_gateway=True,
        restart_policy="reconcile_on_ready" if module.key in (
            "voice", "tickets", "membership", "moderation", "events", "store") else "reinitialize",
        failure_policy="report_error_keep_data",
    ) for module in MODULES)


def describe(states: dict[str, str]) -> list[dict]:
    return [{
        "key": spec.key, "title": spec.title,
        "state": states.get(spec.key, "uninitialized"),
        "soft_dependencies": list(SOFT_DEPENDENCIES.get(spec.key, ())),
        "hard_dependencies": list(spec.hard_dependencies),
        "settings_prefix": spec.settings_prefix,
        "requires_gateway": spec.requires_gateway,
        "restart_policy": spec.restart_policy,
        "failure_policy": spec.failure_policy,
    } for spec in contracts()]


def validate() -> None:
    keys = {m.key for m in MODULES}
    assert len(keys) == len(MODULES), "Duplicate module key"
    for key, dependencies in SOFT_DEPENDENCIES.items():
        if key not in keys or any(dep not in keys or dep == key for dep in dependencies):
            raise ValueError("Недопустимые зависимости модулей")
