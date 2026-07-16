from types import SimpleNamespace
from typing import Any, cast

from chatbot.features.metrics.mapping import ConversationRow
from chatbot.features.metrics.sync import fetch_tickets, run_sync
from chatbot.platform.config import Settings


def _settings() -> Settings:
    return cast(
        Settings,
        SimpleNamespace(
            zendesk_subdomain="proton",
            zendesk_email="me@x.com",
            zendesk_api_token="tok",
            bigquery_project_id="proj",
            bigquery_dataset="ds",
            bigquery_conversations_table="conversations",
        ),
    )


def test_fetch_tickets_pages_until_end_of_stream() -> None:
    pages: dict[str, dict[str, Any]] = {
        "https://proton.zendesk.com/api/v2/incremental/tickets.json?start_time=0&include=metric_sets": {
            "tickets": [{"id": 1}],
            "next_page": "PAGE2",
            "end_of_stream": False,
        },
        "PAGE2": {"tickets": [{"id": 2}], "next_page": None, "end_of_stream": True},
    }
    seen: list[str] = []

    def get_page(url: str) -> dict[str, Any]:
        seen.append(url)
        return pages[url]

    tickets = fetch_tickets(_settings(), get_page=get_page)
    assert [t["id"] for t in tickets] == [1, 2]
    assert len(seen) == 2  # stopped at end_of_stream


def test_run_sync_maps_and_loads() -> None:
    tickets: list[dict[str, Any]] = [
        {"id": 1, "external_id": "whatsapp-+60", "status": "solved", "tags": ["csat_5"]},
        {"id": 2, "status": "closed", "tags": []},  # skipped (no external_id, no csat)
    ]
    loaded: list[ConversationRow] = []

    result = run_sync(
        _settings(),
        fetch=lambda _s: tickets,
        load=lambda _s, rows: loaded.extend(rows),
    )
    assert result == {"tickets": 2, "rows": 1}
    assert len(loaded) == 1
    assert loaded[0].channel == "WhatsApp" and loaded[0].csat_score == 5


def test_fetch_tickets_sideloads_metric_sets() -> None:
    seen: list[str] = []

    def fake_get_page(url: str) -> dict[str, object]:
        seen.append(url)
        return {"tickets": [], "end_of_stream": True}

    settings = _settings()
    fetch_tickets(settings, get_page=fake_get_page)
    assert "include=metric_sets" in seen[0]
