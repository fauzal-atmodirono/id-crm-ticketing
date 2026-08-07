"""SLA scan inbox scoping: one inbox, several, all, or a malformed value.

``fetch_conversations`` used to hard-filter to the single
``settings.chatwoot_inbox_id``, so a tenant whose email inbox differs from
that one var never had its email conversations scanned -- the email
escalation timers (feedback #2) could never fire. ``sla_inbox_ids`` lets a
scan cover several inboxes, or every inbox ("*"), while an unset value keeps
pre-existing behaviour byte-identical.
"""

from __future__ import annotations

from typing import Any

from chatbot.features.metrics.sync import fetch_conversations


class _Settings:
    chatwoot_api_url = "http://cw"
    chatwoot_account_id = 1
    chatwoot_api_token = "t"
    chatwoot_inbox_id = 2
    sla_inbox_ids = ""


def _recording_get_page(urls: list[str]):
    def _get_page(url: str) -> dict[str, Any]:
        urls.append(url)
        return {"data": {"payload": []}}

    return _get_page


def test_defaults_to_the_single_configured_inbox() -> None:
    """Empty sla_inbox_ids (the default) must produce the exact same request
    URL as before this change -- a tenant that never sets the new var sees
    no behavioural change at all."""
    urls: list[str] = []
    settings = _Settings()
    fetch_conversations(settings, get_page=_recording_get_page(urls))
    assert urls == ["http://cw/api/v1/accounts/1/conversations?status=all&inbox_id=2&page=1"]


def test_scans_each_listed_inbox() -> None:
    urls: list[str] = []
    settings = _Settings()
    settings.sla_inbox_ids = "2, 4"
    fetch_conversations(settings, get_page=_recording_get_page(urls))
    assert urls == [
        "http://cw/api/v1/accounts/1/conversations?status=all&inbox_id=2&page=1",
        "http://cw/api/v1/accounts/1/conversations?status=all&inbox_id=4&page=1",
    ]


def test_all_inboxes_when_explicitly_set_to_star() -> None:
    urls: list[str] = []
    settings = _Settings()
    settings.sla_inbox_ids = "*"
    fetch_conversations(settings, get_page=_recording_get_page(urls))
    assert urls == ["http://cw/api/v1/accounts/1/conversations?status=all&page=1"]
    assert all("inbox_id=" not in u for u in urls)


def test_malformed_value_degrades_to_single_inbox() -> None:
    """Garbage in sla_inbox_ids must not crash the scan -- it degrades to
    the same single-inbox behaviour as an unset value."""
    urls: list[str] = []
    settings = _Settings()
    settings.sla_inbox_ids = "not-a-number, , abc"
    fetch_conversations(settings, get_page=_recording_get_page(urls))
    assert urls == ["http://cw/api/v1/accounts/1/conversations?status=all&inbox_id=2&page=1"]


def test_unicode_digit_degrades_to_single_inbox_instead_of_raising() -> None:
    """``str.isdigit()`` is True for non-ASCII digit characters (e.g. the
    superscript '²') that ``int()`` cannot parse, so guarding the
    ``int()`` conversion with ``.isdigit()`` alone lets a stray Unicode
    character crash the whole scan with a ValueError. Must degrade safely
    to single-inbox behaviour instead, same as any other malformed value."""
    urls: list[str] = []
    settings = _Settings()
    settings.sla_inbox_ids = "²"
    fetch_conversations(settings, get_page=_recording_get_page(urls))
    assert urls == ["http://cw/api/v1/accounts/1/conversations?status=all&inbox_id=2&page=1"]


def test_duplicate_ids_are_deduped_not_fetched_twice() -> None:
    """A duplicated id in sla_inbox_ids (e.g. copy-paste config error) must
    not fetch the same inbox twice -- fetch_conversations has no dedup of
    its own, and its results feed /tasks/mine directly, so a duplicate here
    would show up as visibly duplicated task rows in the My-Tasks UI."""
    urls: list[str] = []
    settings = _Settings()
    settings.sla_inbox_ids = "4,4"
    fetch_conversations(settings, get_page=_recording_get_page(urls))
    assert urls == ["http://cw/api/v1/accounts/1/conversations?status=all&inbox_id=4&page=1"]


def test_multi_inbox_scan_keeps_each_inboxs_conversations_correctly_attributed() -> None:
    """A multi-inbox scan must not let inbox A's conversations leak into
    inbox B's results (or vice versa) via the defensive post-fetch filter --
    each scope entry's own inbox_id must gate its own batch."""
    settings = _Settings()
    settings.sla_inbox_ids = "2,4"

    def get_page(url: str) -> dict[str, Any]:
        if "inbox_id=2" in url and "page=1" in url:
            # Chatwoot ignoring the query param: batch mixes inbox 2 and 4.
            return {
                "data": {
                    "payload": [
                        {"id": 1, "inbox_id": 2},
                        {"id": 2, "inbox_id": 4},
                    ]
                }
            }
        if "inbox_id=4" in url and "page=1" in url:
            return {
                "data": {
                    "payload": [
                        {"id": 3, "inbox_id": 4},
                        {"id": 4, "inbox_id": 2},
                    ]
                }
            }
        return {"data": {"payload": []}}

    convs = fetch_conversations(settings, get_page=get_page)
    # Each pass keeps only its own scope's inbox_id (or missing inbox_id) --
    # id=2 (foreign, seen while scanning inbox 2) and id=4 (foreign, seen
    # while scanning inbox 4) are both dropped by the defensive filter.
    assert [c["id"] for c in convs] == [1, 3]
