"""Discord staff role classification for in-bot management, not website login.

The Obsidian web console itself uses only a local password. Staff role labels
are still needed when transferring Discord tickets and applications.
"""
OWNER_ROLE = 1439347293391159652
ADMIN_ROLE = 1440528252933374072
MODER_ROLE = 1439347293391159651
TICKET_ROLE = 1440528284504035358


def site_role(member, guild) -> str | None:
    roles = {role.id for role in member.roles}
    if OWNER_ROLE in roles or member.id == guild.owner_id:
        return "owner"
    if ADMIN_ROLE in roles:
        return "admin"
    if MODER_ROLE in roles:
        return "moder"
    if TICKET_ROLE in roles:
        return "support"
    return None
