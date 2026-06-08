"""Tool schemas — what the LLM sees for the CRM tools."""

from __future__ import annotations

_STATUS_DESC = (
    "Contact lifecycle stage. One of: 'lead' (new/unqualified), 'active' "
    "(in conversation), 'customer' (converted), 'archived' (inactive). "
    "Defaults to 'lead'."
)

CRM_FIND_CONTACT_SCHEMA = {
    "name": "crm_find_contact",
    "description": (
        "Search the CRM for contacts. Use this before creating a contact to "
        "avoid duplicates, or to look up who you're talking to. You can search "
        "by free text (matches name, email, or messaging handle) and/or filter "
        "by status or tag, OR resolve an exact messaging handle with "
        "platform+user_id. Returns matching contacts with their status, tags, "
        "emails, and linked handles."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Free-text search over name, email, and handle user_id.",
            },
            "platform": {
                "type": "string",
                "description": "Messaging platform for an exact handle lookup (e.g. 'telegram', 'whatsapp'). Use with user_id.",
            },
            "user_id": {
                "type": "string",
                "description": "Platform user id for an exact handle lookup. Use with platform.",
            },
            "status": {"type": "string", "description": "Filter by status."},
            "tag": {"type": "string", "description": "Filter by a single tag."},
            "limit": {
                "type": "integer",
                "description": "Max contacts to return (default 20).",
            },
        },
        "required": [],
    },
}

CRM_CREATE_CONTACT_SCHEMA = {
    "name": "crm_create_contact",
    "description": (
        "Create a new CRM contact. Call crm_find_contact first to avoid "
        "duplicates. Use this to capture a new lead from a conversation, an "
        "email, or manual entry. Link the messaging identity via platform+"
        "user_id so their conversation history attaches automatically."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "display_name": {
                "type": "string",
                "description": "The contact's name or best available label. Required.",
            },
            "status": {"type": "string", "description": _STATUS_DESC},
            "source": {
                "type": "string",
                "description": "Where the contact came from (e.g. 'email', 'manual', 'whatsapp', 'telegram').",
            },
            "notes": {"type": "string", "description": "Free-form notes about the contact."},
            "emails": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Email addresses for the contact.",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Free-form tags for categorization (e.g. 'vip', 'grow-shop').",
            },
            "platform": {
                "type": "string",
                "description": "Messaging platform to link (e.g. 'telegram', 'whatsapp'). Use with user_id.",
            },
            "user_id": {
                "type": "string",
                "description": "Platform user id to link. Use with platform.",
            },
        },
        "required": ["display_name"],
    },
}

CRM_UPDATE_CONTACT_SCHEMA = {
    "name": "crm_update_contact",
    "description": (
        "Update an existing CRM contact by id (get the id from "
        "crm_find_contact). Use this to change status (e.g. promote a lead to "
        "customer), edit notes, or set tags/emails. Provide only the fields "
        "you want to change. Passing 'tags' or 'emails' REPLACES the whole "
        "set; use 'add_tags' to append without replacing. Set 'platform'+"
        "'user_id' to link an additional messaging handle."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "The contact id to update. Required."},
            "display_name": {"type": "string", "description": "New display name."},
            "status": {"type": "string", "description": _STATUS_DESC},
            "notes": {"type": "string", "description": "Replace the notes field."},
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Replace ALL tags with this set.",
            },
            "add_tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Append these tags, keeping existing ones.",
            },
            "emails": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Replace ALL emails with this set.",
            },
            "platform": {
                "type": "string",
                "description": "Messaging platform to link (with user_id).",
            },
            "user_id": {
                "type": "string",
                "description": "Platform user id to link (with platform).",
            },
        },
        "required": ["id"],
    },
}
