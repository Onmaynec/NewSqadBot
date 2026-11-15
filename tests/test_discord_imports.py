"""CI импортирует реальные Cogs с установленной discord.py 2.7.1."""
import pytest

pytest.importorskip("discord")


def test_v02_extensions_can_be_imported():
    from app.modules.audit_logs import AuditLogs
    from app.modules.membership import ApplicationForm, Membership, ReviewView
    from app.modules.moderation import Moderation

    assert AuditLogs and ApplicationForm and Membership and ReviewView and Moderation
