from __future__ import annotations

import json
from unittest.mock import AsyncMock

from chatbot.features.chat.pic_registry import PicEntry, PicRegistry, build_pic_registry
from chatbot.features.chat.pic_store import PicRecord
from chatbot.platform.config import Settings


def _settings(pic_map: dict | None = None) -> Settings:
    raw = json.dumps(pic_map) if pic_map else ""
    return Settings(_env_file=None, pic_map_json=raw)


async def test_lookup_returns_matching_entry() -> None:
    s = _settings(
        {
            "apps": {
                "pic_name": "Alice Tan",
                "pic_email": "alice@proton.my",
                "pic_whatsapp": "+60123456789",
                "chatwoot_team_id": 3,
            }
        }
    )
    reg = build_pic_registry(s)
    entry = await reg.lookup("apps")
    assert entry is not None
    assert entry.pic_name == "Alice Tan"
    assert entry.pic_email == "alice@proton.my"
    assert entry.pic_whatsapp == "+60123456789"
    assert entry.chatwoot_team_id == 3


async def test_lookup_normalises_department_key() -> None:
    s = _settings(
        {
            "apps": {
                "pic_name": "A",
                "pic_email": "a@b.my",
                "pic_whatsapp": "+601",
            }
        }
    )
    reg = build_pic_registry(s)
    # dept label from Chatwoot is "dept_apps" — caller strips prefix; test raw key
    assert await reg.lookup("Apps") is not None  # case insensitive
    assert await reg.lookup("APPS") is not None


async def test_lookup_returns_none_for_unknown_dept() -> None:
    s = _settings(
        {
            "apps": {
                "pic_name": "A",
                "pic_email": "a@b.my",
                "pic_whatsapp": "+601",
            }
        }
    )
    reg = build_pic_registry(s)
    assert await reg.lookup("charging") is None


async def test_empty_pic_map_json_returns_none() -> None:
    s = Settings(_env_file=None, pic_map_json="")
    reg = build_pic_registry(s)
    assert await reg.lookup("apps") is None


async def test_malformed_json_returns_none_not_crash() -> None:
    s = Settings(_env_file=None, pic_map_json="{bad json")
    reg = build_pic_registry(s)
    assert await reg.lookup("apps") is None


async def test_missing_optional_chatwoot_team_id_defaults_to_none() -> None:
    s = _settings(
        {
            "apps": {
                "pic_name": "A",
                "pic_email": "a@b.my",
                "pic_whatsapp": "+601",
            }
        }
    )
    reg = build_pic_registry(s)
    entry = await reg.lookup("apps")
    assert entry is not None
    assert entry.chatwoot_team_id is None


async def test_lookup_parses_cc_emails_list() -> None:
    s = _settings(
        {
            "apps": {
                "pic_name": "Alice Tan",
                "pic_email": "alice@proton.my",
                "pic_whatsapp": "+60123456789",
                "cc_emails": ["manager@proton.my", "team-dl@proton.my"],
            }
        }
    )
    reg = build_pic_registry(s)
    entry = await reg.lookup("apps")
    assert entry is not None
    assert entry.cc_emails == ["manager@proton.my", "team-dl@proton.my"]


async def test_cc_emails_defaults_to_empty_when_absent() -> None:
    s = _settings(
        {
            "apps": {
                "pic_name": "A",
                "pic_email": "a@b.my",
                "pic_whatsapp": "+601",
            }
        }
    )
    reg = build_pic_registry(s)
    entry = await reg.lookup("apps")
    assert entry is not None
    assert entry.cc_emails == []


async def test_cc_emails_ignored_when_not_a_list() -> None:
    """A malformed cc_emails (e.g. a bare string) degrades to empty, not a crash."""
    s = _settings(
        {
            "apps": {
                "pic_name": "A",
                "pic_email": "a@b.my",
                "pic_whatsapp": "+601",
                "cc_emails": "manager@proton.my",
            }
        }
    )
    reg = build_pic_registry(s)
    entry = await reg.lookup("apps")
    assert entry is not None
    assert entry.cc_emails == []


# ---------------------------------------------------------------------------
# Task 2: store-first lookup, env-var fallback
# ---------------------------------------------------------------------------


async def test_lookup_returns_store_record_when_present() -> None:
    """When the store has an entry for the department, it wins over the table."""
    store = AsyncMock()
    store.get.return_value = PicRecord(
        department="apps",
        pic_name="Store Person",
        pic_email="store@proton.my",
        pic_whatsapp="+60111111111",
        cc_emails=["cc@proton.my"],
    )
    table = {
        "apps": PicEntry(
            pic_name="Legacy Person",
            pic_email="legacy@proton.my",
            pic_whatsapp="+60122222222",
        )
    }
    reg = PicRegistry(table, store=store)

    entry = await reg.lookup("apps")

    assert entry is not None
    assert entry.pic_name == "Store Person"
    assert entry.pic_email == "store@proton.my"
    assert entry.pic_whatsapp == "+60111111111"
    assert entry.cc_emails == ["cc@proton.my"]
    store.get.assert_awaited_once_with("apps")


async def test_lookup_falls_back_to_legacy_table_when_store_has_no_entry() -> None:
    """Store present but returns None for this department -> legacy table used."""
    store = AsyncMock()
    store.get.return_value = None
    table = {
        "apps": PicEntry(
            pic_name="Legacy Person",
            pic_email="legacy@proton.my",
            pic_whatsapp="+60122222222",
        )
    }
    reg = PicRegistry(table, store=store)

    entry = await reg.lookup("apps")

    assert entry is not None
    assert entry.pic_name == "Legacy Person"
    assert entry.pic_email == "legacy@proton.my"


async def test_lookup_returns_none_when_neither_store_nor_table_has_entry() -> None:
    store = AsyncMock()
    store.get.return_value = None
    reg = PicRegistry({}, store=store)

    assert await reg.lookup("apps") is None


async def test_lookup_works_without_a_store_configured() -> None:
    """No store passed (store=None) -> falls straight through to the legacy table,
    unchanged behaviour for tenants that never configured Firestore."""
    table = {
        "apps": PicEntry(
            pic_name="Legacy Person",
            pic_email="legacy@proton.my",
            pic_whatsapp="+60122222222",
        )
    }
    reg = PicRegistry(table)

    entry = await reg.lookup("apps")

    assert entry is not None
    assert entry.pic_name == "Legacy Person"
