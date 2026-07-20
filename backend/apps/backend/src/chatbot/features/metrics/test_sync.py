from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

from chatbot.features.metrics.mapping import ConversationRow
from chatbot.features.metrics.sync import fetch_conversations, load_conversations, run_sync
from chatbot.platform.config import Settings


def _settings() -> Settings:
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
            "labels": ["csat_5", "category_aftersales"],
            "meta": {"sender": {"identifier": "whatsapp-+60"}},
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
    assert loaded[0].category == "aftersales" and loaded[0].division == "Aftersales"


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
