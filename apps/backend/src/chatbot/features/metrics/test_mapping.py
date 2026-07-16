import pytest

from chatbot.features.metrics.mapping import (
    ConversationRow,
    channel_from_external_id,
    map_ticket_to_row,
)


def _ticket(**kw: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": 55,
        "external_id": "whatsapp-+60123",
        "status": "solved",
        "tags": [],
        "created_at": "2026-06-21T09:14:00Z",
        "updated_at": "2026-06-21T09:31:00Z",
        "assignee_id": None,
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
        (None, "Other"),
    ],
)
def test_channel_from_external_id_public(external_id: str | None, expected: str) -> None:
    assert channel_from_external_id(external_id) == expected


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


@pytest.mark.parametrize(
    "tag,expected",
    [("nps_0", 0), ("nps_6", 6), ("nps_7", 7), ("nps_9", 9), ("nps_10", 10)],
)
def test_nps_from_tag(tag: str, expected: int) -> None:
    row = map_ticket_to_row(_ticket(tags=[tag]))
    assert row is not None and row.nps_score == expected


def test_nps_tag_out_of_range_ignored() -> None:
    row = map_ticket_to_row(_ticket(tags=["nps_11"]))
    assert row is not None and row.nps_score is None


def test_no_nps_tag_is_none() -> None:
    row = map_ticket_to_row(_ticket(tags=["foo"]))
    assert row is not None and row.nps_score is None


def test_keep_nps_only_ticket_even_without_external_id() -> None:
    row = map_ticket_to_row({"id": 3, "status": "solved", "tags": ["nps_8"]})
    assert row is not None and row.channel == "Other" and row.nps_score == 8


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
        nps_score=None,
        category=None,
        subcategory=None,
        division=None,
        department=None,
        pic=None,
        agent_id=None,
        sla_minutes=None,
        sla_deadline=None,
    )


def test_skip_non_conversation() -> None:
    # no external_id AND no csat tag → not a conversation
    assert map_ticket_to_row({"id": 1, "status": "closed", "tags": []}) is None


def test_keep_csat_only_ticket_even_without_external_id() -> None:
    row = map_ticket_to_row({"id": 2, "status": "solved", "tags": ["csat_5"]})
    assert row is not None and row.channel == "Other" and row.csat_score == 5


def test_division_derived_from_category_tag() -> None:
    row = map_ticket_to_row(_ticket(tags=["category_aftersales"]))
    assert row is not None and row.category == "aftersales"
    assert row.division == "Aftersales"


def test_subcategory_and_department_and_pic_tags() -> None:
    row = map_ticket_to_row(_ticket(tags=["subcat_battery", "dept_service", "pic_alice"]))
    assert row is not None
    assert row.subcategory == "battery"
    assert row.department == "service"
    assert row.pic == "alice"


def test_pic_falls_back_to_assignee_when_no_pic_tag() -> None:
    row = map_ticket_to_row(_ticket(assignee_id=7007, tags=[]))
    assert row is not None and row.agent_id == "7007" and row.pic == "7007"


def test_sla_minutes_and_deadline_from_tag() -> None:
    row = map_ticket_to_row(_ticket(tags=["sla_480"], created_at="2026-06-21T09:00:00Z"))
    assert row is not None and row.sla_minutes == 480
    assert row.sla_deadline == "2026-06-21T17:00:00+00:00"


def test_dimension_fields_default_none() -> None:
    row = map_ticket_to_row(_ticket(tags=[]))
    assert row is not None
    assert row.category is None and row.division is None and row.sla_minutes is None


def test_metric_set_timing_and_reopens() -> None:
    row = map_ticket_to_row(
        _ticket(
            created_at="2026-06-21T09:00:00Z",
            metric_set={
                "solved_at": "2026-06-21T10:30:00Z",
                "reopens": 2,
                "reply_time_in_minutes": {"calendar": 15},
            },
        )
    )
    assert row is not None
    assert row.resolved_at == "2026-06-21T10:30:00Z"
    assert row.reopen_count == 2
    assert row.first_response_at == "2026-06-21T09:15:00+00:00"


def test_no_metric_set_leaves_timing_none() -> None:
    row = map_ticket_to_row(_ticket())
    assert row is not None
    assert row.resolved_at is None and row.reopen_count is None
    assert row.first_response_at is None
