"""CI импортирует реальные Cogs с установленной discord.py 2.7.1."""
import pytest

pytest.importorskip("discord")


def test_v02_extensions_can_be_imported():
    from app.modules.audit_logs import AuditLogs
    from app.modules.leveling import Leveling
    from app.modules.membership import ApplicationForm, Membership, ReviewView
    from app.modules.moderation import Moderation
    from app.modules.profiles import Profiles
    from app.modules.roles import RolePanelView, Roles
    from app.modules.tickets import TicketActions, TicketOpenView, Tickets
    from app.modules.welcome import Welcome
    assert AuditLogs and ApplicationForm and Membership and ReviewView and Moderation
    assert RolePanelView and Roles and Welcome and Tickets and TicketOpenView and TicketActions
    assert Leveling and Profiles


def test_engagement_modules_import_and_commands_registered():
    from app.modules.achievements import Achievements
    from app.modules.quests import Quests
    from app.panels.botsettings import HomeView
    from app.panels.settings_overview import PAGE_OPTIONS

    assert Achievements.achievements.name == "achievements"
    assert Quests.quests.name == "quests"
    assert {"achievements", "quests"} <= {row[0] for row in PAGE_OPTIONS}

    class Stub:
        config = type("Cfg", (), {"owner_ids": frozenset()})()

    import discord
    view = HomeView(Stub(), 1)
    labels = ({item.label for item in view.children if isinstance(item, discord.ui.Button)}
              | {option.label for item in view.children if isinstance(item, discord.ui.Select)
                 for option in item.options})
    assert {"Задания", "Достижения", "Экономика", "Все настройки"} <= labels
