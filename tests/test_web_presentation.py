"""Regression checks for the four web console presentation surfaces."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from app.web.audit_presentation import describe_value, present_audit


class FakeGuild:
    def __init__(self):
        self.members = {111111111111111111: SimpleNamespace(display_name="Модератор Ника")}
        self.roles = {222222222222222222: SimpleNamespace(name="Поддержка")}
        self.channels = {333333333333333333: SimpleNamespace(name="объявления")}

    def get_member(self, uid):
        return self.members.get(uid)

    def get_role(self, uid):
        return self.roles.get(uid)

    def get_channel(self, uid):
        return self.channels.get(uid)


def record(action: str, details: dict, actor: int = 111111111111111111):
    return SimpleNamespace(
        id=7, action=action, data_json=json.dumps(details, ensure_ascii=False),
        actor_id=actor, created_at=1_700_000_000,
    )


def test_audit_names_and_structured_fields():
    guild = FakeGuild()
    row = record("announcements.published", {
        "target_id": 333333333333333333,
        "details": "Автор 111111111111111111; роль 222222222222222222; канал 333333333333333333",
    })
    result = present_audit(row, guild)
    assert result["title"] == "Опубликовано объявление"
    assert result["category"] == "communication"
    assert result["actor_name"] == "Модератор Ника"
    assert result["fields"][0]["value"] == "#объявления"
    assert "Модератор Ника" in result["summary"]
    assert "@Поддержка" in result["summary"]
    assert "#объявления" in result["summary"]
    # Keep raw factual data for backwards-compatible API clients.
    assert result["details"]["target_id"] == 333333333333333333


def test_audit_local_settings_have_readable_names():
    result = present_audit(
        record("settings.updated", {"key": "farewell_enabled", "source": "local_web"}, actor=0),
        FakeGuild(),
    )
    assert result["actor_name"] == "Локальная панель"
    assert result["title"] == "Изменена настройка"
    assert result["summary"] == "Сообщения о выходе"
    assert result["fields"][1]["value"] == "Локальная панель"


def test_audit_unknown_entities_are_not_misidentified():
    guild = FakeGuild()
    assert describe_value(guild, 10, "target_id") == "10"
    assert describe_value(guild, "444444444444444444", "user_id").startswith("Недоступный объект")
    assert describe_value(guild, True) == "Да"
    result = present_audit(
        record("custom.event", {"details": "Message 444444444444444444"}, actor=444444444444444444),
        guild,
    )
    assert result["category"] == "system"
    assert "444444444444444444" in result["actor_name"]
    assert "444444444444444444" in result["summary"]


def test_product_pages_keep_existing_api_contracts_and_accessibility():
    static = Path(__file__).resolve().parents[1] / "app" / "web" / "static"
    portal = (static / "portal.js").read_text(encoding="utf-8")
    app = (static / "app.js").read_text(encoding="utf-8")
    html = (static / "index.html").read_text(encoding="utf-8")
    css = (static / "style.css").read_text(encoding="utf-8")

    for endpoint in ('"/api/portal/dashboard"', '"/api/portal/audit"',
                     '"/api/portal/stats?period="'):
        assert endpoint in portal
    for fragment in ("analytics-chart-line", "audit-search", "audit-expand",
                     "overview-kpi"):
        assert fragment in portal and fragment in css
    for identifier in ('id="login-guild-icon"', 'id="login-mode-label"',
                       'id="login-security-note"', 'id="discord-login"',
                       'id="overview-guild-name"', 'id="overview-live"'):
        assert identifier in html
    assert 'login-mode-label' in app
    assert '[hidden]{display:none!important}' in css


def test_obsidian_shell_assets_and_role_aware_search():
    """The Obsidian layer must preserve backend APIs and restrict UI navigation."""
    root = Path(__file__).resolve().parents[1]
    static = root / "app" / "web" / "static"
    html = (static / "index.html").read_text(encoding="utf-8")
    css = (static / "obsidian.css").read_text(encoding="utf-8")
    js = (static / "obsidian.js").read_text(encoding="utf-8")
    app_js = (static / "app.js").read_text(encoding="utf-8")
    server = (root / "app" / "web" / "server.py").read_text(encoding="utf-8")

    for asset in ("obsidian.css", "obsidian.js"):
        assert f'/static/{asset}' in html
        assert f'"{asset}"' in server
    for node_id in ("open-command", "command-palette", "command-input",
                    "command-results", "close-command", "mobile-nav-backdrop"):
        assert f'id="{node_id}"' in html
    assert 'role="dialog" aria-modal="true"' in html
    assert 'aria-controls="command-results"' in html
    assert 'aria-expanded="false"' in html
    assert "prefers-reduced-motion: reduce" in css
    assert ".navigation .nav-item.active" in css

    # The command palette gets destinations from the nav produced for the current role.
    assert 'querySelectorAll(".nav-item")' in js
    assert 'window.portalRole?.() !== "owner"' in js
    assert 'allowed.has(spec.group)' in js
    assert 'window.portalFindSetting(choice.group, choice.key)' in js
    assert 'ui.role === "owner" && ui.data?.groups.includes(group)' in app_js
    assert "if (!exists || !await navigate(group)) return false;" in app_js
    assert 'if (!await confirmDiscard()) return false;' in app_js
