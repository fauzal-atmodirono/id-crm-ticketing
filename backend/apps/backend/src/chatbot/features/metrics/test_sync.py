from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

from chatbot.features.metrics.mapping import ConversationRow
from chatbot.features.metrics.sync import fetch_conversations, load_conversations, run_sync
from chatbot.platform.config import Settings


def _settings(*, metrics_exclude_demo_seed: bool = False) -> Settings:
    return cast(
        Settings,
        SimpleNamespace(
            chatwoot_api_url="https://cw.example.com",
            chatwoot_api_token="tok",
            chatwoot_account_id=1,
            chatwoot_inbox_id=7,
            bigquery_project_id="proj",
            bigquery_dataset="ds",
            bigquery_conversations_table="conversations",
            metrics_exclude_demo_seed=metrics_exclude_demo_seed,
        ),
    )


def _conv(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"id": 1, "inbox_id": 7, "status": "resolved", "labels": ["csat_5"]}
    base.update(kw)
    return base


def test_fetch_conversations_pages_until_empty() -> None:
    pages: dict[str, dict[str, Any]] = {
        "https://cw.example.com/api/v1/accounts/1/conversations?status=all&inbox_id=7&page=1": {
            "data": {"payload": [_conv(id=1)]}
        },
        "https://cw.example.com/api/v1/accounts/1/conversations?status=all&inbox_id=7&page=2": {
            "data": {"payload": [_conv(id=2)]}
        },
        "https://cw.example.com/api/v1/accounts/1/conversations?status=all&inbox_id=7&page=3": {
            "data": {"payload": []}
        },
    }
    seen: list[str] = []

    def get_page(url: str) -> dict[str, Any]:
        seen.append(url)
        return pages[url]

    convs = fetch_conversations(_settings(), get_page=get_page)
    assert [c["id"] for c in convs] == [1, 2]
    assert len(seen) == 3  # stopped when page 3 came back empty


def test_fetch_conversations_filters_foreign_inbox() -> None:
    def get_page(url: str) -> dict[str, Any]:
        if "page=1" in url:
            return {"data": {"payload": [_conv(id=1, inbox_id=7), _conv(id=9, inbox_id=99)]}}
        return {"data": {"payload": []}}

    convs = fetch_conversations(_settings(), get_page=get_page)
    assert [c["id"] for c in convs] == [1]  # inbox 99 dropped


def test_fetch_conversations_targets_inbox_and_account() -> None:
    seen: list[str] = []

    def get_page(url: str) -> dict[str, Any]:
        seen.append(url)
        return {"data": {"payload": []}}

    fetch_conversations(_settings(), get_page=get_page)
    assert "/api/v1/accounts/1/conversations" in seen[0]
    assert "inbox_id=7" in seen[0]
    assert "status=all" in seen[0]


def test_run_sync_maps_and_loads() -> None:
    convs: list[dict[str, Any]] = [
        {
            "id": 1,
            "status": "resolved",
            "labels": ["csat_5"],
            "meta": {"sender": {"identifier": "whatsapp-+60"}},
            "custom_attributes": {"case_category": "aftersales"},
        },
        {"id": 2, "status": "resolved", "labels": []},  # skipped: no source, no csat/nps, no labels
    ]
    loaded: list[ConversationRow] = []

    result = run_sync(
        _settings(),
        fetch=lambda _s: convs,
        load=lambda _s, rows: loaded.extend(rows),
    )
    assert result == {"conversations": 2, "rows": 1}
    assert len(loaded) == 1
    assert loaded[0].channel == "WhatsApp" and loaded[0].csat_score == 5
    # category comes from custom_attributes (not labels); division is derived from it
    assert loaded[0].category == "aftersales" and loaded[0].division == "Aftersales"


def test_run_sync_excludes_demo_seed_conversations_when_flag_enabled() -> None:
    """Package D risk mitigation: a conversation carrying custom_attributes.
    demo_seed must never reach the warehouse once METRICS_EXCLUDE_DEMO_SEED
    is on."""
    convs: list[dict[str, Any]] = [
        _conv(id=1, custom_attributes={"demo_seed": "batch-2026-08-04"}),
        _conv(id=2, custom_attributes={}),
    ]
    loaded: list[ConversationRow] = []

    result = run_sync(
        _settings(metrics_exclude_demo_seed=True),
        fetch=lambda _s: convs,
        load=lambda _s, rows: loaded.extend(rows),
    )

    assert [r.conversation_id for r in loaded] == ["2"]
    assert result == {"conversations": 1, "rows": 1}


def test_run_sync_includes_demo_seed_conversations_when_flag_disabled() -> None:
    """Default-off must be byte-identical to today: a demo_seed-marked
    conversation still syncs when METRICS_EXCLUDE_DEMO_SEED is unset/false."""
    convs: list[dict[str, Any]] = [
        _conv(id=1, custom_attributes={"demo_seed": "batch-2026-08-04"}),
        _conv(id=2, custom_attributes={}),
    ]
    loaded: list[ConversationRow] = []

    result = run_sync(
        _settings(metrics_exclude_demo_seed=False),
        fetch=lambda _s: convs,
        load=lambda _s, rows: loaded.extend(rows),
    )

    assert sorted(r.conversation_id for r in loaded) == ["1", "2"]
    assert result == {"conversations": 2, "rows": 2}


def test_load_conversations_includes_dealer_field() -> None:
    """load_conversations must pass the dealer column in every row dict."""
    row = ConversationRow(
        conversation_id="1",
        channel="WhatsApp",
        created_at=None,
        updated_at=None,
        status="resolved",
        resolved_by="bot",
        csat_score=None,
        nps_score=None,
        dealer="surabaya_utara",
    )

    class FakeSettings:
        bigquery_project_id = "p"
        bigquery_dataset = "d"
        bigquery_conversations_table = "conversations"

    with mock.patch("chatbot.features.metrics.sync.bigquery") as bq:
        bq.Client.return_value.create_dataset.return_value = None
        job = mock.MagicMock()
        bq.Client.return_value.load_table_from_json.return_value = job
        job.result.return_value = None
        bq.LoadJobConfig.return_value = mock.MagicMock()
        bq.SchemaField = mock.MagicMock()

        load_conversations(FakeSettings(), [row])  # type: ignore[arg-type]
        call_args = bq.Client.return_value.load_table_from_json.call_args
        json_rows = call_args[0][0]

    assert json_rows, "no rows captured"
    assert json_rows[0]["dealer"] == "surabaya_utara"


def test_load_conversations_includes_case_type_and_vehicle_model_fields() -> None:
    """load_conversations must pass case_type/vehicle_model in every row dict."""
    row = ConversationRow(
        conversation_id="1",
        channel="WhatsApp",
        created_at=None,
        updated_at=None,
        status="resolved",
        resolved_by="bot",
        csat_score=None,
        nps_score=None,
        case_type="Inquiry",
        vehicle_model="e.MAS 7",
    )

    class FakeSettings:
        bigquery_project_id = "p"
        bigquery_dataset = "d"
        bigquery_conversations_table = "conversations"

    with mock.patch("chatbot.features.metrics.sync.bigquery") as bq:
        bq.Client.return_value.create_dataset.return_value = None
        job = mock.MagicMock()
        bq.Client.return_value.load_table_from_json.return_value = job
        job.result.return_value = None
        bq.LoadJobConfig.return_value = mock.MagicMock()
        bq.SchemaField = mock.MagicMock()

        load_conversations(FakeSettings(), [row])  # type: ignore[arg-type]
        call_args = bq.Client.return_value.load_table_from_json.call_args
        json_rows = call_args[0][0]

    assert json_rows, "no rows captured"
    assert json_rows[0]["case_type"] == "Inquiry"
    assert json_rows[0]["vehicle_model"] == "e.MAS 7"


def test_run_sync_augments_rows_with_working_minutes() -> None:
    conv = {
        "id": 99, "inbox_id": 7, "status": "resolved",
        "created_at": 1783328400,  # 2026-07-06T09:00:00Z (Monday)
        "last_activity_at": 1783337400,  # 2026-07-06T11:30:00Z
        "labels": [], "custom_attributes": {},
        "meta": {"sender": {"id": 1, "identifier": "whatsapp-+60123"}},
        "first_reply_created_at": 1783330200,  # 2026-07-06T09:30:00Z
    }
    inbox_hours = {
        "working_hours_enabled": True, "timezone": "UTC",
        "working_hours": [{"day_of_week": 1, "open_hour": 9, "open_minutes": 0,
                            "close_hour": 18, "close_minutes": 0,
                            "open_all_day": False, "closed_all_day": False}],
    }
    loaded_rows: list[ConversationRow] = []
    result = run_sync(
        _settings(),
        fetch=lambda _s: [conv],
        fetch_inbox=lambda _s, _inbox_id: inbox_hours,
        load=lambda _s, rows: loaded_rows.extend(rows),
    )
    assert result["rows"] == 1
    assert loaded_rows[0].resolution_working_minutes == 150


def test_run_sync_fetches_each_inbox_once_and_degrades_failed_inbox_to_calendar_time() -> None:
    """Two conversations share inbox 7 (fetched once); inbox 8's fetch fails and
    must degrade only that row to calendar-time minutes, never crash the sync."""
    convs: list[dict[str, Any]] = [
        {
            "id": 1, "inbox_id": 7, "status": "resolved",
            "created_at": 1783328400,  # 2026-07-06T09:00:00Z (Monday)
            "last_activity_at": 1783337400,  # 2026-07-06T11:30:00Z (+150m)
            "labels": [], "custom_attributes": {},
            "meta": {"sender": {"id": 1, "identifier": "whatsapp-+60123"}},
        },
        {
            "id": 2, "inbox_id": 7, "status": "resolved",
            "created_at": 1783328400,
            "last_activity_at": 1783337400,
            "labels": [], "custom_attributes": {},
            "meta": {"sender": {"id": 1, "identifier": "whatsapp-+60123"}},
        },
        {
            "id": 3, "inbox_id": 8, "status": "resolved",
            "created_at": 1783328400,
            "last_activity_at": 1783337400,
            "labels": [], "custom_attributes": {},
            "meta": {"sender": {"id": 1, "identifier": "whatsapp-+60123"}},
        },
    ]
    inbox_hours = {
        "working_hours_enabled": True, "timezone": "UTC",
        "working_hours": [{"day_of_week": 1, "open_hour": 9, "open_minutes": 0,
                            "close_hour": 18, "close_minutes": 0,
                            "open_all_day": False, "closed_all_day": False}],
    }
    fetch_calls: list[int] = []

    def fetch_inbox(_s: Any, inbox_id: int) -> dict[str, Any] | None:
        fetch_calls.append(inbox_id)
        if inbox_id == 8:
            raise RuntimeError("boom")
        return inbox_hours

    loaded_rows: list[ConversationRow] = []
    result = run_sync(
        _settings(),
        fetch=lambda _s: convs,
        fetch_inbox=fetch_inbox,
        load=lambda _s, rows: loaded_rows.extend(rows),
    )
    assert result["rows"] == 3
    # inbox 7 fetched exactly once despite two conversations referencing it
    assert fetch_calls.count(7) == 1
    assert fetch_calls.count(8) == 1
    # both inbox-7 rows got working-hours-aware timing (150 == calendar here anyway
    # because window falls fully within business hours)
    assert loaded_rows[0].resolution_working_minutes == 150
    assert loaded_rows[1].resolution_working_minutes == 150
    # inbox-8 row must NOT crash the sync; it degrades to calendar-time minutes
    assert loaded_rows[2].resolution_working_minutes == 150
