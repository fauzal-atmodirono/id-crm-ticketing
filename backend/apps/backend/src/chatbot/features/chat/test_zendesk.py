from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from chatbot.features.chat.adapters.zendesk import ZendeskAdapter


def _adapter(tag: str = "") -> ZendeskAdapter:
    settings = SimpleNamespace(
        zendesk_subdomain="proton",
        zendesk_email="agent@proton.test",
        zendesk_api_token="tok",
        zendesk_requester_domain="proton.devoteam.example",
        zendesk_customer_name_prefix="Proton AI Customer",
        zendesk_ticket_tag=tag,
        handoff_store="memory",
    )
    return ZendeskAdapter(settings)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_create_ticket_emits_classification_tags() -> None:
    """Zendesk adapter must append classification tags to the ticket payload."""
    captured: dict[str, Any] = {}

    class _Resp:
        def raise_for_status(self) -> None: ...

        def json(self) -> dict[str, Any]:
            return {"ticket": {"id": 999}}

    class _Client:
        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *a: object) -> None: ...

        async def post(
            self,
            url: str,
            json: object,
            headers: object,
            timeout: object,  # noqa: ASYNC109
        ) -> _Resp:
            captured["url"] = url
            captured["json"] = json
            return _Resp()

    adapter = _adapter(tag="proton_ai")

    with patch("chatbot.features.chat.adapters.zendesk.httpx.AsyncClient", _Client):
        ticket_id = await adapter.create_ticket(
            session_id="whatsapp-+60123",
            title="t",
            body="b",
            urgency="high",
            category="Aftersales",
            subcategory="Battery Issue",
            division="Aftersales",
            sla_minutes=480,
        )

    assert ticket_id == "999"
    tags: list[str] = captured["json"]["ticket"]["tags"]
    assert "proton_ai" in tags  # base tag preserved
    assert "category_aftersales" in tags
    assert "subcat_battery_issue" in tags
    assert "division_aftersales" in tags
    assert "sla_480" in tags


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "urgency,expected_priority",
    [
        ("low", "low"),
        ("medium", "normal"),
        ("high", "high"),
        ("urgent", "urgent"),
        ("nonsense", "normal"),  # unknown urgency defaults to normal
    ],
)
async def test_create_ticket_priority_matches_urgency(urgency: str, expected_priority: str) -> None:
    """Routing/SLA views key on ticket priority — lock the urgency→priority contract."""
    captured: dict[str, Any] = {}

    class _Resp:
        def raise_for_status(self) -> None: ...

        def json(self) -> dict[str, Any]:
            return {"ticket": {"id": 1}}

    class _Client:
        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *a: object) -> None: ...

        async def post(
            self,
            url: str,
            json: object,
            headers: object,
            timeout: object,  # noqa: ASYNC109
        ) -> _Resp:
            captured["json"] = json
            return _Resp()

    with patch("chatbot.features.chat.adapters.zendesk.httpx.AsyncClient", _Client):
        await _adapter().create_ticket(
            session_id="whatsapp-+60123", title="t", body="b", urgency=urgency
        )

    assert captured["json"]["ticket"]["priority"] == expected_priority


@pytest.mark.asyncio
async def test_create_ticket_no_classification_tags_when_absent() -> None:
    """When no classification args provided and no base tag, ticket has no tags key."""
    captured: dict[str, Any] = {}

    class _Resp:
        def raise_for_status(self) -> None: ...

        def json(self) -> dict[str, Any]:
            return {"ticket": {"id": 1}}

    class _Client:
        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *a: object) -> None: ...

        async def post(
            self,
            url: str,
            json: object,
            headers: object,
            timeout: object,  # noqa: ASYNC109
        ) -> _Resp:
            captured["json"] = json
            return _Resp()

    adapter = _adapter(tag="")

    with patch("chatbot.features.chat.adapters.zendesk.httpx.AsyncClient", _Client):
        await adapter.create_ticket(
            session_id="whatsapp-+60123",
            title="t",
            body="b",
            urgency="normal",
        )

    assert "tags" not in captured["json"]["ticket"]
