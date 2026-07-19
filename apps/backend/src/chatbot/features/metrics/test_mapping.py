import pytest

from chatbot.features.metrics.mapping import (
    ConversationRow,
    channel_from_external_id,
    map_chatwoot_conversation_to_row,
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


def test_malformed_created_at_yields_none_deadline() -> None:
    """A malformed created_at should not raise; sla_deadline should be None."""
    row = map_ticket_to_row(_ticket(created_at="not-a-date", tags=["sla_480"]))
    assert row is not None
    assert row.sla_deadline is None


def test_malformed_metric_set_fields_skip_gracefully() -> None:
    """Malformed reopens and reply_time_in_minutes should skip gracefully."""
    row = map_ticket_to_row(
        _ticket(metric_set={"reopens": "abc", "reply_time_in_minutes": {"calendar": "x"}})
    )
    assert row is not None
    assert row.reopen_count is None
    assert row.first_response_at is None


def test_zero_assignee_id_is_kept() -> None:
    """assignee_id=0 should be kept, not treated as falsy."""
    row = map_ticket_to_row(_ticket(assignee_id=0))
    assert row is not None
    assert row.agent_id == "0"


# --- Chatwoot conversation mapping -------------------------------------------

# 2026-06-21T09:00:00Z == epoch 1750496400; +90min resolve == 09:31:40 for
# last_activity below; keep timestamps simple and assert derived values.
_CREATED_EPOCH = 1782032400  # 2026-06-21T09:00:00+00:00
_RESOLVED_EPOCH = 1782036000  # 2026-06-21T10:00:00+00:00


def _conv(**kw: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": 88,
        "status": "resolved",
        "labels": [],
        "created_at": _CREATED_EPOCH,
        "last_activity_at": _RESOLVED_EPOCH,
        "meta": {"sender": {"identifier": "whatsapp-+60123"}},
    }
    base.update(kw)
    return base


def test_chatwoot_basic_fields_and_channel() -> None:
    row = map_chatwoot_conversation_to_row(_conv())
    assert row is not None
    assert row.conversation_id == "88"
    assert row.channel == "WhatsApp"
    assert row.created_at == "2026-06-21T09:00:00+00:00"
    assert row.updated_at == "2026-06-21T10:00:00+00:00"
    assert row.status == "resolved"
    assert row.resolved_by == "bot"  # resolved == bot/agent closed it
    assert row.resolved_at == "2026-06-21T10:00:00+00:00"


def test_chatwoot_open_status_is_agent_and_not_resolved() -> None:
    row = map_chatwoot_conversation_to_row(_conv(status="open"))
    assert row is not None
    assert row.resolved_by == "agent"
    assert row.resolved_at is None


def test_chatwoot_dimension_labels_parsed() -> None:
    row = map_chatwoot_conversation_to_row(
        _conv(labels=["category_aftersales", "subcat_battery", "dept_service", "sla_480"])
    )
    assert row is not None
    assert row.category == "aftersales"
    assert row.subcategory == "battery"
    assert row.department == "service"
    assert row.division == "Aftersales"  # derived from category
    assert row.sla_minutes == 480
    assert row.sla_deadline == "2026-06-21T17:00:00+00:00"


def test_chatwoot_explicit_division_label_wins() -> None:
    row = map_chatwoot_conversation_to_row(
        _conv(labels=["category_aftersales", "division_charging"])
    )
    assert row is not None and row.division == "charging"


def test_chatwoot_csat_and_nps_from_labels() -> None:
    row = map_chatwoot_conversation_to_row(_conv(labels=["csat_4", "nps_9"]))
    assert row is not None and row.csat_score == 4 and row.nps_score == 9


def test_chatwoot_agent_id_from_meta_assignee() -> None:
    row = map_chatwoot_conversation_to_row(
        _conv(meta={"assignee": {"id": 7007}, "sender": {"identifier": "whatsapp-+60"}})
    )
    assert row is not None and row.agent_id == "7007" and row.pic == "7007"


def test_chatwoot_sla_from_custom_attributes_fallback() -> None:
    row = map_chatwoot_conversation_to_row(_conv(custom_attributes={"sla_minutes": 240}))
    assert row is not None and row.sla_minutes == 240


def test_chatwoot_first_reply_maps_to_first_response_at() -> None:
    row = map_chatwoot_conversation_to_row(_conv(first_reply_created_at=_RESOLVED_EPOCH))
    assert row is not None and row.first_response_at == "2026-06-21T10:00:00+00:00"


def test_chatwoot_source_id_top_level() -> None:
    conv = _conv()
    conv.pop("meta")
    conv["source_id"] = "email-42"
    row = map_chatwoot_conversation_to_row(conv)
    assert row is not None and row.channel == "Email"


def test_chatwoot_skip_empty_conversation() -> None:
    # no source_id, no csat/nps, no labels → not a conversation worth a row
    assert map_chatwoot_conversation_to_row({"id": 1, "status": "open", "labels": []}) is None


def test_chatwoot_keep_label_only_conversation() -> None:
    row = map_chatwoot_conversation_to_row(
        {"id": 2, "status": "resolved", "labels": ["category_sales"]}
    )
    assert row is not None and row.channel == "Other" and row.category == "sales"


def test_chatwoot_malformed_timestamp_is_none() -> None:
    row = map_chatwoot_conversation_to_row(_conv(created_at="nope", last_activity_at=None))
    assert row is not None
    assert row.created_at is None and row.updated_at is None
    assert row.resolved_at is None  # resolved but no timestamp to attribute


def test_chatwoot_output_matches_conversation_row_type() -> None:
    row = map_chatwoot_conversation_to_row(_conv(labels=[]))
    assert isinstance(row, ConversationRow)
    assert row.reopen_count is None  # zammad-timing TODO: best-effort None


# --- Phase-3: dealer dimension + reopen_count wiring ---

def test_chatwoot_dealer_label_parsed() -> None:
    """dealer_<slug> label maps to row.dealer."""
    row = map_chatwoot_conversation_to_row(
        _conv(labels=["category_aftersales", "dealer_surabaya_utara"])
    )
    assert row is not None
    assert row.dealer == "surabaya_utara"


def test_chatwoot_no_dealer_label_is_none() -> None:
    row = map_chatwoot_conversation_to_row(_conv(labels=["category_sales"]))
    assert row is not None
    assert row.dealer is None


def test_chatwoot_multiple_dealer_labels_takes_first() -> None:
    """When two dealer_ labels are present, the first encountered wins."""
    row = map_chatwoot_conversation_to_row(
        _conv(labels=["dealer_abc", "dealer_xyz"])
    )
    assert row is not None
    assert row.dealer == "abc"


def test_chatwoot_reopen_count_from_additional_attributes() -> None:
    """reopen_count is read from additional_attributes.reopen_count (Zammad write-back)."""
    row = map_chatwoot_conversation_to_row(
        _conv(additional_attributes={"reopen_count": 3})
    )
    assert row is not None
    assert row.reopen_count == 3


def test_chatwoot_reopen_count_zero_is_kept() -> None:
    row = map_chatwoot_conversation_to_row(
        _conv(additional_attributes={"reopen_count": 0})
    )
    assert row is not None
    assert row.reopen_count == 0


def test_chatwoot_reopen_count_missing_stays_none() -> None:
    """When additional_attributes has no reopen_count key, field stays None."""
    row = map_chatwoot_conversation_to_row(
        _conv(additional_attributes={"some_other_key": "value"})
    )
    assert row is not None
    assert row.reopen_count is None


def test_chatwoot_reopen_count_no_additional_attributes_stays_none() -> None:
    """When no additional_attributes at all, reopen_count stays None."""
    conv = _conv()
    conv.pop("additional_attributes", None)
    row = map_chatwoot_conversation_to_row(conv)
    assert row is not None
    assert row.reopen_count is None


def test_chatwoot_reopen_count_malformed_string_is_none() -> None:
    """A non-numeric reopen_count must not raise; yields None."""
    row = map_chatwoot_conversation_to_row(
        _conv(additional_attributes={"reopen_count": "bad"})
    )
    assert row is not None
    assert row.reopen_count is None


def test_chatwoot_dealer_in_conversation_row_type() -> None:
    """ConversationRow dataclass has a dealer field."""
    row = map_chatwoot_conversation_to_row(_conv(labels=["dealer_jakarta"]))
    assert row is not None
    assert isinstance(row, ConversationRow)
    assert hasattr(row, "dealer")
    assert row.dealer == "jakarta"
