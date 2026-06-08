"""Tests for the CRM plugin's SQLite store."""

from __future__ import annotations

import pytest

from plugins.crm import crm_db


@pytest.fixture()
def db(tmp_path):
    return tmp_path / "crm.db"


def test_create_and_get_contact(db):
    c = crm_db.create_contact(
        "Ada Lovelace",
        status="customer",
        source="manual",
        notes="VIP",
        emails=["ada@example.com", "ada@example.com"],  # dupe collapses
        tags=["vip", "early"],
        handles=[{"platform": "Telegram", "user_id": "123"}],
        db_path=db,
    )
    assert c["display_name"] == "Ada Lovelace"
    assert c["status"] == "customer"
    assert c["emails"] == ["ada@example.com"]
    assert sorted(c["tags"]) == ["early", "vip"]
    # platform is normalized to lowercase
    assert c["handles"] == [{"platform": "telegram", "user_id": "123"}]

    again = crm_db.get_contact(c["id"], db_path=db)
    assert again == c


def test_blank_name_rejected(db):
    with pytest.raises(ValueError):
        crm_db.create_contact("   ", db_path=db)


def test_invalid_status_falls_back_to_lead(db):
    c = crm_db.create_contact("Bob", status="nonsense", db_path=db)
    assert c["status"] == "lead"


def test_find_by_handle(db):
    c = crm_db.create_contact(
        "Carol", handles=[{"platform": "whatsapp", "user_id": "+44999"}], db_path=db
    )
    found = crm_db.find_contact_by_handle("whatsapp", "+44999", db_path=db)
    assert found is not None and found["id"] == c["id"]
    # case-insensitive platform match
    assert crm_db.find_contact_by_handle("WhatsApp", "+44999", db_path=db)["id"] == c["id"]
    assert crm_db.find_contact_by_handle("telegram", "+44999", db_path=db) is None


def test_handle_belongs_to_one_contact(db):
    a = crm_db.create_contact(
        "A", handles=[{"platform": "telegram", "user_id": "777"}], db_path=db
    )
    b = crm_db.create_contact("B", db_path=db)
    # Re-link the same handle to B — it should move, not duplicate.
    crm_db.update_contact(
        b["id"], handles=[{"platform": "telegram", "user_id": "777"}], db_path=db
    )
    owner = crm_db.find_contact_by_handle("telegram", "777", db_path=db)
    assert owner["id"] == b["id"]
    assert crm_db.get_contact(a["id"], db_path=db)["handles"] == []


def test_update_scalar_and_preserve_collections(db):
    c = crm_db.create_contact("Dora", tags=["lead-src"], emails=["d@x.com"], db_path=db)
    updated = crm_db.update_contact(c["id"], status="active", db_path=db)
    assert updated["status"] == "active"
    # tags/emails untouched because not passed
    assert updated["tags"] == ["lead-src"]
    assert updated["emails"] == ["d@x.com"]


def test_update_missing_returns_none(db):
    assert crm_db.update_contact("nope", status="active", db_path=db) is None


def test_list_filters_and_search(db):
    crm_db.create_contact("Alice", status="lead", tags=["grow"],
                          emails=["alice@farm.io"], db_path=db)
    crm_db.create_contact("Albert", status="customer", tags=["grow", "vip"],
                          handles=[{"platform": "telegram", "user_id": "alb"}], db_path=db)
    crm_db.create_contact("Zed", status="lead", db_path=db)

    assert crm_db.list_contacts(db_path=db)["total"] == 3
    assert crm_db.list_contacts(status="lead", db_path=db)["total"] == 2
    assert crm_db.list_contacts(tag="vip", db_path=db)["total"] == 1
    # text query hits name, email, and handle user_id
    assert crm_db.list_contacts(q="al", db_path=db)["total"] == 2  # Alice, Albert
    assert crm_db.list_contacts(q="farm.io", db_path=db)["total"] == 1
    assert crm_db.list_contacts(q="alb", db_path=db)["total"] == 1


def test_delete_cascades(db):
    c = crm_db.create_contact(
        "Eve", emails=["e@x.com"], tags=["t"],
        handles=[{"platform": "telegram", "user_id": "eve"}], db_path=db,
    )
    assert crm_db.delete_contact(c["id"], db_path=db) is True
    assert crm_db.get_contact(c["id"], db_path=db) is None
    assert crm_db.find_contact_by_handle("telegram", "eve", db_path=db) is None
    assert crm_db.all_handles(db_path=db) == {}
    assert crm_db.delete_contact(c["id"], db_path=db) is False


def test_all_handles_index(db):
    a = crm_db.create_contact(
        "A", handles=[{"platform": "telegram", "user_id": "1"},
                      {"platform": "whatsapp", "user_id": "2"}], db_path=db
    )
    idx = crm_db.all_handles(db_path=db)
    assert idx == {"telegram:1": a["id"], "whatsapp:2": a["id"]}
