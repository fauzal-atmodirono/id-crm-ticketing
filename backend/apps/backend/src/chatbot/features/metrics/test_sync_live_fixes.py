# ruff: noqa: ARG001, ARG005  -- injected fakes match the real callables' signatures
"""Regression tests for the proton metrics-sync defects found live (2026-11-09).

Each test pins one root cause seen against tenant proton's real Chatwoot API
shapes (ids/counts only were inspected):

1. The metrics sync reused the SLA engine's inbox scope (`sla_inbox_ids=3,5`),
   so the API inbox (57 of 116 conversations) never reached BigQuery.
2. A conversation with no `meta.sender.identifier` and no labels was dropped,
   and its channel became "Other" -- yet every Chatwoot list item carries
   `meta.channel` (and the inbox carries the Twilio `medium`).
3. Chatwoot-native CSAT (`csat_survey_responses`) was never read; only the
   backend's `csat_<n>` labels were.
4. Chatwoot reports "no first reply yet" as `first_reply_created_at: 0`, which
   mapped to 1970-01-01 and produced ~-28.5M-minute response times.
6. `ai_cost_view_ddls` was never called, so `v_ai_token_usage` never existed
   and `/metrics/ai-cost` 404'd although `token_usage` had rows.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

from chatbot.features.metrics import sync
from chatbot.features.metrics.mapping import (
    apply_working_hours,
    map_chatwoot_conversation_to_row,
)
from chatbot.platform.config import Settings


def _settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {
        "chatwoot_api_url": "https://cw.example.com",
        "chatwoot_api_token": "tok",
        "chatwoot_account_id": 1,
        "chatwoot_inbox_id": 1,
        "sla_inbox_ids": "3,5",
        "bigquery_project_id": "proj",
        "bigquery_dataset": "ds",
        "bigquery_conversations_table": "conversations",
        "metrics_exclude_demo_seed": False,
    }
    base.update(kw)
    return cast(Settings, SimpleNamespace(**base))


def _cw_conv(**kw: Any) -> dict[str, Any]:
    """A Chatwoot list item shaped like proton's (no identifier, no labels)."""
    base: dict[str, Any] = {
        "id": 106,
        "inbox_id": 3,
        "status": "resolved",
        "labels": [],
        "created_at": 1_762_600_000,
        "last_activity_at": 1_762_603_600,
        "first_reply_created_at": 1_762_600_600,
        "custom_attributes": {"lifecycle_state": "closed"},
        "meta": {"channel": "Channel::TwilioSms", "sender": {}},
    }
    base.update(kw)
    return base


# --- 1: account-wide scope ---------------------------------------------------


def test_metrics_fetch_is_account_wide_regardless_of_sla_scope() -> None:
    urls: list[str] = []

    def get_page(url: str) -> dict[str, Any]:
        urls.append(url)
        if "page=1" in url:
            return {"data": {"payload": [_cw_conv(id=1, inbox_id=1), _cw_conv(id=2, inbox_id=3)]}}
        return {"data": {"payload": []}}

    convs = sync.fetch_metrics_conversations(_settings(), get_page=get_page)
    assert [c["id"] for c in convs] == [1, 2]
    assert all("inbox_id=" not in u for u in urls)


def test_run_sync_defaults_to_the_account_wide_fetch() -> None:
    with mock.patch.object(sync, "fetch_metrics_conversations", return_value=[]) as m:
        sync.run_sync(
            _settings(),
            fetch_inbox=lambda s, i: None,
            fetch_csat=lambda s: {},
            load=lambda s, rows: None,
        )
    m.assert_called_once()


# --- 2: keep + classify conversations by Chatwoot channel --------------------


def test_conversation_without_identifier_or_labels_is_kept() -> None:
    assert map_chatwoot_conversation_to_row(_cw_conv()) is not None


def test_channel_from_meta_channel_email() -> None:
    row = map_chatwoot_conversation_to_row(_cw_conv(meta={"channel": "Channel::Email"}))
    assert row is not None and row.channel == "Email"


def test_channel_twilio_whatsapp_medium_from_inbox() -> None:
    inbox = {"channel_type": "Channel::TwilioSms", "medium": "whatsapp"}
    row = map_chatwoot_conversation_to_row(_cw_conv(), inbox=inbox)
    assert row is not None and row.channel == "WhatsApp"


def test_channel_twilio_without_medium_is_sms() -> None:
    row = map_chatwoot_conversation_to_row(_cw_conv())
    assert row is not None and row.channel == "SMS"


def test_identifier_prefix_still_wins_for_api_inbox() -> None:
    conv = _cw_conv(
        inbox_id=1, meta={"channel": "Channel::Api", "sender": {"identifier": "whatsapp-x"}}
    )
    row = map_chatwoot_conversation_to_row(conv, inbox={"channel_type": "Channel::Api"})
    assert row is not None and row.channel == "WhatsApp"


def test_run_sync_uses_inbox_medium_for_channel() -> None:
    loaded: list[Any] = []
    sync.run_sync(
        _settings(),
        fetch=lambda s: [_cw_conv()],
        fetch_inbox=lambda s, i: {"channel_type": "Channel::TwilioSms", "medium": "whatsapp"},
        fetch_csat=lambda s: {},
        load=lambda s, rows: loaded.extend(rows),
    )
    assert [r.channel for r in loaded] == ["WhatsApp"]


# --- 3: native Chatwoot CSAT -------------------------------------------------


def test_run_sync_fills_csat_from_native_survey_responses() -> None:
    loaded: list[Any] = []
    sync.run_sync(
        _settings(),
        fetch=lambda s: [_cw_conv(id=106), _cw_conv(id=107), _cw_conv(id=35, labels=["csat_5"])],
        fetch_inbox=lambda s, i: None,
        fetch_csat=lambda s: {106: 3, 35: 1},
        load=lambda s, rows: loaded.extend(rows),
    )
    by_id = {r.conversation_id: r.csat_score for r in loaded}
    assert by_id == {"106": 3, "107": None, "35": 5}  # the label wins when both exist


def test_run_sync_survives_a_failing_csat_fetch() -> None:
    def boom(s: Settings) -> dict[int, int]:
        raise RuntimeError("csat down")

    result = sync.run_sync(
        _settings(),
        fetch=lambda s: [_cw_conv()],
        fetch_inbox=lambda s, i: None,
        fetch_csat=boom,
        load=lambda s, rows: None,
    )
    assert result["rows"] == 1


def test_fetch_csat_ratings_pages_and_keeps_only_ids_and_ratings() -> None:
    urls: list[str] = []

    def get_page(url: str) -> Any:
        urls.append(url)
        if "page=1" in url:
            return [
                {"conversation_id": 106, "rating": 3, "feedback_message": "x"},
                {"conversation_id": 136, "rating": 5},
                {"conversation_id": None, "rating": 4},
                {"conversation_id": 140, "rating": 9},
            ]
        return []

    ratings = sync.fetch_csat_ratings(_settings(), get_page=get_page)
    assert ratings == {106: 3, 136: 5}
    assert "/api/v1/accounts/1/csat_survey_responses" in urls[0]
    assert len(urls) == 2


def test_fetch_csat_ratings_fails_open() -> None:
    def get_page(url: str) -> Any:
        raise RuntimeError("403")

    assert sync.fetch_csat_ratings(_settings(), get_page=get_page) == {}


# --- 4: first_reply_created_at == 0 means "no reply yet" ---------------------


def test_zero_first_reply_is_not_1970() -> None:
    row = map_chatwoot_conversation_to_row(_cw_conv(first_reply_created_at=0))
    assert row is not None and row.first_response_at is None
    assert apply_working_hours(row, None).first_response_working_minutes is None


def test_first_reply_before_creation_is_dropped() -> None:
    row = map_chatwoot_conversation_to_row(_cw_conv(first_reply_created_at=1_000))
    assert row is not None and row.first_response_at is None


# --- 6: the AI-cost views are actually created --------------------------------


def _view_settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {
        "bigquery_project_id": "proj",
        "bigquery_dataset": "ds",
        "bigquery_conversations_table": "conversations",
        "resolution_sla_targets_json": "",
        "reporting_timezone": "UTC",
        "csat_by_agent_enabled": False,
        "csat_ranking_min_samples": 5,
        "kb_score_floor": 0.5,
        "anomaly_hourly_enabled": False,
        "anomaly_hourly_zscore_k": 3.0,
        "anomaly_hourly_min_baseline": 5,
        "ai_cost_reporting_enabled": True,
    }
    base.update(kw)
    return cast(Settings, SimpleNamespace(**base))


def _run_ensure_views(settings: Settings, fail_on: str | None = None) -> list[str]:
    ran: list[str] = []

    class _Job:
        def result(self) -> None:
            return None

    class _Client:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        def query(self, ddl: str) -> _Job:
            if fail_on and fail_on in ddl:
                raise RuntimeError("404 token_usage not found")
            ran.append(ddl)
            return _Job()

    with mock.patch.object(sync.bigquery, "Client", _Client):
        sync.ensure_views(settings)
    return ran


def test_ensure_views_creates_ai_cost_views_when_enabled() -> None:
    ran = _run_ensure_views(_view_settings())
    assert any("v_ai_token_usage" in d for d in ran)
    assert any(".v_ai_cost`" in d for d in ran)


def test_ensure_views_skips_ai_cost_views_when_disabled() -> None:
    ran = _run_ensure_views(_view_settings(ai_cost_reporting_enabled=False))
    assert not any("v_ai_token_usage" in d for d in ran)


def test_ai_cost_view_failure_does_not_abort_ensure_views() -> None:
    ran = _run_ensure_views(_view_settings(), fail_on="v_ai_token_usage")
    assert any("v_csat" in d for d in ran)  # conversation views still created
