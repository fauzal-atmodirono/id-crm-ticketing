from __future__ import annotations

import json

from chatbot.features.chat.pic_registry import build_pic_registry
from chatbot.platform.config import Settings


def _settings(pic_map: dict | None = None) -> Settings:
    raw = json.dumps(pic_map) if pic_map else ""
    return Settings(_env_file=None, pic_map_json=raw)


def test_lookup_returns_matching_entry() -> None:
    s = _settings({
        "apps": {
            "pic_name": "Alice Tan",
            "pic_email": "alice@proton.my",
            "pic_whatsapp": "+60123456789",
            "zammad_group": "Apps-Support",
            "chatwoot_team_id": 3,
        }
    })
    reg = build_pic_registry(s)
    entry = reg.lookup("apps")
    assert entry is not None
    assert entry.pic_name == "Alice Tan"
    assert entry.pic_email == "alice@proton.my"
    assert entry.pic_whatsapp == "+60123456789"
    assert entry.zammad_group == "Apps-Support"
    assert entry.chatwoot_team_id == 3


def test_lookup_normalises_department_key() -> None:
    s = _settings({"apps": {"pic_name": "A", "pic_email": "a@b.my",
                             "pic_whatsapp": "+601", "zammad_group": "G"}})
    reg = build_pic_registry(s)
    # dept label from Chatwoot is "dept_apps" — caller strips prefix; test raw key
    assert reg.lookup("Apps") is not None   # case insensitive
    assert reg.lookup("APPS") is not None


def test_lookup_returns_none_for_unknown_dept() -> None:
    s = _settings({"apps": {"pic_name": "A", "pic_email": "a@b.my",
                             "pic_whatsapp": "+601", "zammad_group": "G"}})
    reg = build_pic_registry(s)
    assert reg.lookup("charging") is None


def test_empty_pic_map_json_returns_none() -> None:
    s = Settings(_env_file=None, pic_map_json="")
    reg = build_pic_registry(s)
    assert reg.lookup("apps") is None


def test_malformed_json_returns_none_not_crash() -> None:
    s = Settings(_env_file=None, pic_map_json="{bad json")
    reg = build_pic_registry(s)
    assert reg.lookup("apps") is None


def test_missing_optional_chatwoot_team_id_defaults_to_none() -> None:
    s = _settings({"apps": {"pic_name": "A", "pic_email": "a@b.my",
                             "pic_whatsapp": "+601", "zammad_group": "G"}})
    reg = build_pic_registry(s)
    entry = reg.lookup("apps")
    assert entry is not None
    assert entry.chatwoot_team_id is None
