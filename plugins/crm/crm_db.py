"""SQLite-backed CRM store — contacts + light status/tags, per profile.

The DB lives at ``<HERMES_HOME>/crm.db``. Because ``HERMES_HOME`` is
``<root>/profiles/<name>`` in profile mode, each profile gets its own
contacts automatically — the ``grow-shop`` client's customers never mix
with the ``default`` profile's. Override the path with ``HERMES_CRM_DB``
(used by tests).

State ownership note
--------------------
This store owns *people and their categorization*. It does NOT own
conversations: a contact links to channels via ``contact_handles
(platform, user_id)``, and the conversation history is resolved at read
time from the gateway session store + ``SessionDB``. Deleting a contact
never touches any message data.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Iterable, Optional

# Single status per contact; free-form tags handle finer categorization.
STATUSES = ("lead", "active", "customer", "archived")
DEFAULT_STATUS = "lead"

_INITIALIZED_PATHS: set[str] = set()


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

def crm_db_path() -> Path:
    """Return the path to this profile's ``crm.db``.

    ``HERMES_CRM_DB`` overrides everything (tests). Otherwise the DB lives
    directly under the active profile's Hermes home, which gives per-profile
    isolation for free.
    """
    override = os.environ.get("HERMES_CRM_DB", "").strip()
    if override:
        return Path(override).expanduser()
    from hermes_constants import get_hermes_home

    return get_hermes_home() / "crm.db"


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS contacts (
    id           TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'lead',
    source       TEXT,
    notes        TEXT NOT NULL DEFAULT '',
    created_at   INTEGER NOT NULL,
    updated_at   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS contact_emails (
    contact_id TEXT NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    email      TEXT NOT NULL,
    PRIMARY KEY (contact_id, email)
);

-- A handle maps to exactly one contact: the PRIMARY KEY on (platform,
-- user_id) makes resolving an incoming session unambiguous.
CREATE TABLE IF NOT EXISTS contact_handles (
    contact_id TEXT NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    platform   TEXT NOT NULL,
    user_id    TEXT NOT NULL,
    PRIMARY KEY (platform, user_id)
);

CREATE TABLE IF NOT EXISTS contact_tags (
    contact_id TEXT NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    tag        TEXT NOT NULL,
    PRIMARY KEY (contact_id, tag)
);

CREATE INDEX IF NOT EXISTS idx_contacts_status  ON contacts(status);
CREATE INDEX IF NOT EXISTS idx_contacts_updated ON contacts(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_contact_tags_tag ON contact_tags(tag);
CREATE INDEX IF NOT EXISTS idx_contact_emails_email ON contact_emails(email);
CREATE INDEX IF NOT EXISTS idx_contact_handles_cid ON contact_handles(contact_id);
"""


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Open (and initialize on first use) the CRM DB.

    WAL is applied with the shared NFS-safe fallback so the store works on
    network filesystems. Foreign keys are ON so ``ON DELETE CASCADE`` cleans
    up a contact's emails/handles/tags.
    """
    path = db_path if db_path is not None else crm_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        from hermes_state import apply_wal_with_fallback

        apply_wal_with_fallback(conn, db_label=f"crm.db ({path.name})")
    except Exception:
        # Never let WAL setup brick the store; DELETE journaling still works.
        pass
    conn.execute("PRAGMA foreign_keys=ON")
    resolved = str(path.resolve())
    if resolved not in _INITIALIZED_PATHS:
        conn.executescript(SCHEMA_SQL)
        _INITIALIZED_PATHS.add(resolved)
    return conn


@contextlib.contextmanager
def connect_closing(db_path: Optional[Path] = None):
    """Open a connection and guarantee it is closed on exit."""
    conn = connect(db_path)
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> int:
    return int(time.time())


def _norm_status(status: Optional[str]) -> str:
    s = (status or "").strip().lower()
    return s if s in STATUSES else DEFAULT_STATUS


def _clean_list(values: Optional[Iterable[str]]) -> list[str]:
    """Trim, drop blanks, de-dupe (case-insensitive) while preserving order."""
    out: list[str] = []
    seen: set[str] = set()
    for v in values or []:
        s = str(v).strip()
        if not s:
            continue
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def _row_to_contact(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    cid = row["id"]
    emails = [
        r["email"]
        for r in conn.execute(
            "SELECT email FROM contact_emails WHERE contact_id=? ORDER BY email",
            (cid,),
        )
    ]
    handles = [
        {"platform": r["platform"], "user_id": r["user_id"]}
        for r in conn.execute(
            "SELECT platform, user_id FROM contact_handles WHERE contact_id=? "
            "ORDER BY platform, user_id",
            (cid,),
        )
    ]
    tags = [
        r["tag"]
        for r in conn.execute(
            "SELECT tag FROM contact_tags WHERE contact_id=? ORDER BY tag",
            (cid,),
        )
    ]
    return {
        "id": cid,
        "display_name": row["display_name"],
        "status": row["status"],
        "source": row["source"],
        "notes": row["notes"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "emails": emails,
        "handles": handles,
        "tags": tags,
    }


def _replace_children(
    conn: sqlite3.Connection,
    contact_id: str,
    *,
    emails: Optional[Iterable[str]] = None,
    handles: Optional[Iterable[dict]] = None,
    tags: Optional[Iterable[str]] = None,
) -> None:
    """Replace a contact's emails/handles/tags. ``None`` leaves a set as-is."""
    if emails is not None:
        conn.execute("DELETE FROM contact_emails WHERE contact_id=?", (contact_id,))
        for email in _clean_list(emails):
            conn.execute(
                "INSERT OR IGNORE INTO contact_emails(contact_id, email) VALUES(?,?)",
                (contact_id, email),
            )
    if tags is not None:
        conn.execute("DELETE FROM contact_tags WHERE contact_id=?", (contact_id,))
        for tag in _clean_list(tags):
            conn.execute(
                "INSERT OR IGNORE INTO contact_tags(contact_id, tag) VALUES(?,?)",
                (contact_id, tag),
            )
    if handles is not None:
        conn.execute("DELETE FROM contact_handles WHERE contact_id=?", (contact_id,))
        for h in handles:
            platform = str((h or {}).get("platform", "")).strip().lower()
            user_id = str((h or {}).get("user_id", "")).strip()
            if not platform or not user_id:
                continue
            # A handle belongs to exactly one contact — steal it on conflict so
            # re-linking from the "unlinked conversations" inbox is idempotent.
            conn.execute(
                "INSERT INTO contact_handles(contact_id, platform, user_id) "
                "VALUES(?,?,?) ON CONFLICT(platform, user_id) "
                "DO UPDATE SET contact_id=excluded.contact_id",
                (contact_id, platform, user_id),
            )


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def create_contact(
    display_name: str,
    *,
    status: Optional[str] = None,
    source: Optional[str] = None,
    notes: str = "",
    emails: Optional[Iterable[str]] = None,
    handles: Optional[Iterable[dict]] = None,
    tags: Optional[Iterable[str]] = None,
    db_path: Optional[Path] = None,
) -> dict[str, Any]:
    """Create a contact and return the assembled record. Raises ValueError
    if ``display_name`` is blank."""
    name = (display_name or "").strip()
    if not name:
        raise ValueError("display_name is required")
    cid = uuid.uuid4().hex
    now = _now()
    with connect_closing(db_path) as conn:
        conn.execute(
            "INSERT INTO contacts(id, display_name, status, source, notes, "
            "created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
            (cid, name, _norm_status(status), (source or None),
             (notes or ""), now, now),
        )
        _replace_children(conn, cid, emails=emails, handles=handles, tags=tags)
        row = conn.execute("SELECT * FROM contacts WHERE id=?", (cid,)).fetchone()
        return _row_to_contact(conn, row)


def get_contact(contact_id: str, *, db_path: Optional[Path] = None) -> Optional[dict[str, Any]]:
    with connect_closing(db_path) as conn:
        row = conn.execute("SELECT * FROM contacts WHERE id=?", (contact_id,)).fetchone()
        return _row_to_contact(conn, row) if row else None


def find_contact_by_handle(
    platform: str, user_id: str, *, db_path: Optional[Path] = None
) -> Optional[dict[str, Any]]:
    """Resolve the contact owning ``(platform, user_id)``, if any."""
    p = (platform or "").strip().lower()
    u = (user_id or "").strip()
    if not p or not u:
        return None
    with connect_closing(db_path) as conn:
        row = conn.execute(
            "SELECT c.* FROM contacts c JOIN contact_handles h ON h.contact_id=c.id "
            "WHERE h.platform=? AND h.user_id=?",
            (p, u),
        ).fetchone()
        return _row_to_contact(conn, row) if row else None


def list_contacts(
    *,
    status: Optional[str] = None,
    tag: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    db_path: Optional[Path] = None,
) -> dict[str, Any]:
    """List contacts with optional status/tag filters and a text query.

    ``q`` matches display_name, email, or handle user_id (case-insensitive
    substring). Returns ``{"contacts": [...], "total": N}`` where total is
    the count before limit/offset.
    """
    where: list[str] = []
    params: list[Any] = []
    if status:
        where.append("c.status = ?")
        params.append(_norm_status(status))
    if tag:
        where.append(
            "EXISTS (SELECT 1 FROM contact_tags t WHERE t.contact_id=c.id AND t.tag=?)"
        )
        params.append(tag.strip())
    if q:
        like = f"%{q.strip().lower()}%"
        where.append(
            "(LOWER(c.display_name) LIKE ?"
            " OR EXISTS (SELECT 1 FROM contact_emails e WHERE e.contact_id=c.id"
            "            AND LOWER(e.email) LIKE ?)"
            " OR EXISTS (SELECT 1 FROM contact_handles h WHERE h.contact_id=c.id"
            "            AND LOWER(h.user_id) LIKE ?))"
        )
        params.extend([like, like, like])
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    with connect_closing(db_path) as conn:
        total = conn.execute(
            f"SELECT COUNT(*) AS n FROM contacts c {where_sql}", params
        ).fetchone()["n"]
        rows = conn.execute(
            f"SELECT * FROM contacts c {where_sql} "
            "ORDER BY c.updated_at DESC LIMIT ? OFFSET ?",
            (*params, max(1, min(limit, 500)), max(0, offset)),
        ).fetchall()
        return {
            "contacts": [_row_to_contact(conn, r) for r in rows],
            "total": total,
        }


def update_contact(
    contact_id: str,
    *,
    display_name: Optional[str] = None,
    status: Optional[str] = None,
    source: Optional[str] = None,
    notes: Optional[str] = None,
    emails: Optional[Iterable[str]] = None,
    handles: Optional[Iterable[dict]] = None,
    tags: Optional[Iterable[str]] = None,
    db_path: Optional[Path] = None,
) -> Optional[dict[str, Any]]:
    """Patch a contact. Scalar fields update only when not ``None``; the
    emails/handles/tags collections are replaced wholesale only when passed
    (``None`` leaves them untouched). Returns the record, or ``None`` if the
    contact does not exist."""
    with connect_closing(db_path) as conn:
        row = conn.execute("SELECT * FROM contacts WHERE id=?", (contact_id,)).fetchone()
        if not row:
            return None
        sets: list[str] = []
        params: list[Any] = []
        if display_name is not None:
            name = display_name.strip()
            if not name:
                raise ValueError("display_name cannot be blank")
            sets.append("display_name=?")
            params.append(name)
        if status is not None:
            sets.append("status=?")
            params.append(_norm_status(status))
        if source is not None:
            sets.append("source=?")
            params.append(source or None)
        if notes is not None:
            sets.append("notes=?")
            params.append(notes or "")
        if sets:
            sets.append("updated_at=?")
            params.append(_now())
            params.append(contact_id)
            conn.execute(f"UPDATE contacts SET {', '.join(sets)} WHERE id=?", params)
        elif emails is not None or handles is not None or tags is not None:
            # Touch updated_at so a tag/handle-only edit still bumps ordering.
            conn.execute(
                "UPDATE contacts SET updated_at=? WHERE id=?", (_now(), contact_id)
            )
        _replace_children(conn, contact_id, emails=emails, handles=handles, tags=tags)
        row = conn.execute("SELECT * FROM contacts WHERE id=?", (contact_id,)).fetchone()
        return _row_to_contact(conn, row)


def delete_contact(contact_id: str, *, db_path: Optional[Path] = None) -> bool:
    """Delete a contact (and its emails/handles/tags via cascade). Returns
    True if a row was removed. Never touches message/session data."""
    with connect_closing(db_path) as conn:
        cur = conn.execute("DELETE FROM contacts WHERE id=?", (contact_id,))
        return cur.rowcount > 0


def all_handles(*, db_path: Optional[Path] = None) -> dict[str, str]:
    """Return ``{"<platform>:<user_id>": contact_id}`` for every linked
    handle. Used to flag which gateway sessions are already attached to a
    contact (the "unlinked conversations" inbox)."""
    with connect_closing(db_path) as conn:
        return {
            f"{r['platform']}:{r['user_id']}": r["contact_id"]
            for r in conn.execute(
                "SELECT platform, user_id, contact_id FROM contact_handles"
            )
        }
