"""Dealer rows as groups: many member emails, old single-email shape still read."""

from __future__ import annotations

from chatbot.features.chat.escalation_notifier import build_dealer_email_map
from chatbot.features.chat.pic_store import _dealer_record_from_dict
from chatbot.platform.config import Settings


def _settings(**kw: object) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[arg-type]


def test_reads_new_list_shape() -> None:
    rec = _dealer_record_from_dict({"dealer": "komang", "emails": ["a@t", "b@t"]}, "komang")
    assert rec.emails == ["a@t", "b@t"]


def test_reads_legacy_string_shape() -> None:
    rec = _dealer_record_from_dict({"dealer": "komang", "email": "a@t"}, "komang")
    assert rec.emails == ["a@t"]


def test_env_map_accepts_string_and_list() -> None:
    settings = _settings(
        dealer_email_map_json='{"komang": "a@t", "other": ["b@t", "c@t"]}'
    )
    assert build_dealer_email_map(settings) == {"komang": ["a@t"], "other": ["b@t", "c@t"]}


def test_env_map_drops_malformed_entries() -> None:
    settings = _settings(dealer_email_map_json='{"ok": ["a@t"], "bad": 7, "empty": []}')
    assert build_dealer_email_map(settings) == {"ok": ["a@t"]}
