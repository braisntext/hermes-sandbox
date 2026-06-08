"""Hermes CRM plugin.

A thin, native contacts layer: people + light status/tags + their
conversation history. Conversations are NOT copied — they are resolved at
read time from the existing session stores by matching a contact's channel
handles ``(platform, user_id)`` to gateway sessions.

The plugin ships two surfaces:

* an agent-tools surface (``plugin.yaml`` + ``schemas.py`` + ``tools.py``)
  so Hermes can create/find/update contacts mid-conversation, and
* a dashboard surface (``dashboard/``) that adds a ``/crm`` panel tab.

Both surfaces share :mod:`plugins.crm.crm_db`, a per-profile SQLite store
(``<HERMES_HOME>/crm.db``) so each profile keeps its own contacts.

``kind: standalone`` — enable in ``config.yaml`` with ``plugins.enabled:
[crm]`` to expose the agent tools.
"""

from __future__ import annotations

from plugins.crm.schemas import (
    CRM_CREATE_CONTACT_SCHEMA,
    CRM_FIND_CONTACT_SCHEMA,
    CRM_UPDATE_CONTACT_SCHEMA,
)
from plugins.crm.tools import (
    handle_crm_create_contact,
    handle_crm_find_contact,
    handle_crm_update_contact,
)

_TOOLS = (
    ("crm_find_contact", CRM_FIND_CONTACT_SCHEMA, handle_crm_find_contact, "🔎"),
    ("crm_create_contact", CRM_CREATE_CONTACT_SCHEMA, handle_crm_create_contact, "➕"),
    ("crm_update_contact", CRM_UPDATE_CONTACT_SCHEMA, handle_crm_update_contact, "✏️"),
)


def register(ctx) -> None:
    """Register the CRM agent tools. Called once by the plugin loader."""
    for name, schema, handler, emoji in _TOOLS:
        ctx.register_tool(
            name=name,
            toolset="crm",
            schema=schema,
            handler=handler,
            emoji=emoji,
        )
