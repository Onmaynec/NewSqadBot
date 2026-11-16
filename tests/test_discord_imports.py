"""CI импортирует реальные Cogs с установленной discord.py 2.7.1."""
import pytest

pytest.importorskip("discord")


def test_v02_extensions_can_be_imported():
    from app.modules.audit_logs import AuditLogs
    from app.modules.membership import ApplicationForm, Membership, ReviewView
    from app.modules.moderation import Moderation
    from app.modules.roles import RolePanelView, Roles
    from app.modules.tickets import TicketActions, TicketOpenView, Tickets
    from app.modules.welcome import Welcome
    assert AuditLogs and ApplicationForm and Membership and ReviewView and Moderation
    assert RolePanelView and Roles and Welcome and Tickets and TicketOpenView and TicketActions

def test_core_commands_load_without_reserved_names():
    # CogMeta rejects command/group members whose Python names start with bot_ or cog_.
    from app.panels.botsettings import CoreCommands

    assert CoreCommands.diagnostics_group.name == "bot"
    assert CoreCommands.diagnostics_group.get_command("status") is not None
    assert CoreCommands.diagnostics_group.get_command("modules") is not None
