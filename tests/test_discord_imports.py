"""CI импортирует реальные Cogs с установленной discord.py 2.7.1."""
import pytest

pytest.importorskip("discord")


def test_v02_extensions_can_be_imported():
    from app.modules.audit_logs import AuditLogs
    from app.modules.membership import ApplicationForm, Membership, ReviewView
    from app.modules.moderation import Moderation
    from app.modules.roles import RolePanelView, Roles
    from app.modules.welcome import Welcome
    from app.modules.tickets import Tickets, TicketOpenView, TicketActions
    assert AuditLogs and ApplicationForm and Membership and ReviewView and Moderation
    assert RolePanelView and Roles and Welcome and Tickets and TicketOpenView and TicketActions
