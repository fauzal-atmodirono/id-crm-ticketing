import pytest

from chatbot.features.metrics.mapping import ConversationRow, map_ticket_to_row


def _ticket(**kw: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": 55,
        "external_id": "whatsapp-+60123",
        "status": "solved",
        "tags": [],
        "created_at": "2026-06-21T09:14:00Z",
        "updated_at": "2026-06-21T09:31:00Z",
    }
    base.update(kw)
    return base


@pytest.mark.parametrize(
    "external_id,expected",
    [
        ("whatsapp-+60123", "WhatsApp"),
        ("email-55", "Email"),
        ("phone-CA1", "Phone"),
        ("sim-abc", "Web"),
        ("zendesk-conv-9", "Web"),
        ("chatwoot-conv-9", "Web"),
        ("weird-1", "Other"),
    ],
)
def test_channel_from_external_id(external_id: str, expected: str) -> None:
    row = map_ticket_to_row(_ticket(external_id=external_id))
    assert row is not None and row.channel == expected


@pytest.mark.parametrize(
    "status,expected",
    [
        ("new", "agent"),
        ("open", "agent"),
        ("pending", "agent"),
        ("hold", "agent"),
        ("solved", "bot"),
        ("closed", "bot"),
    ],
)
def test_resolved_by_from_status(status: str, expected: str) -> None:
    row = map_ticket_to_row(_ticket(status=status))
    assert row is not None and row.resolved_by == expected


def test_csat_from_tag() -> None:
    row = map_ticket_to_row(_ticket(tags=["foo", "csat_4", "bar"]))
    assert row is not None and row.csat_score == 4


def test_no_csat_tag_is_none() -> None:
    row = map_ticket_to_row(_ticket(tags=["foo"]))
    assert row is not None and row.csat_score is None


def test_csat_tag_out_of_range_ignored() -> None:
    row = map_ticket_to_row(_ticket(tags=["csat_9"]))
    assert row is not None and row.csat_score is None


def test_basic_fields_pass_through() -> None:
    row = map_ticket_to_row(_ticket())
    assert row == ConversationRow(
        conversation_id="55",
        channel="WhatsApp",
        created_at="2026-06-21T09:14:00Z",
        updated_at="2026-06-21T09:31:00Z",
        status="solved",
        resolved_by="bot",
        csat_score=None,
    )


def test_skip_non_conversation() -> None:
    # no external_id AND no csat tag → not a conversation
    assert map_ticket_to_row({"id": 1, "status": "closed", "tags": []}) is None


def test_keep_csat_only_ticket_even_without_external_id() -> None:
    row = map_ticket_to_row({"id": 2, "status": "solved", "tags": ["csat_5"]})
    assert row is not None and row.channel == "Other" and row.csat_score == 5
