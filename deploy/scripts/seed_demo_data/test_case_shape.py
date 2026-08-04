"""What a seeded conversation actually carries: its `source_id`, its
`custom_attributes` object and its labels.

These three are pure functions in `client.py` for one reason -- getting them
wrong is invisible until a live tenant's report is already wrong, and every
one of them was wrong at once in the first cut:

- the `source_id` had no channel token, so
  `backend/.../metrics/mapping.py::channel_from_external_id` bucketed 100% of
  seeded rows as "Other" and the deck's 73/16/9/2 channel split rendered flat;
- `custom_attributes` carried no `dealer_escalated_at`, so the `labels` POST
  fired `conversation_updated`, `agent/app/services/sync.py`'s
  `maybe_stamp_dealer_escalation` wrote one through Chatwoot's *replacing*
  custom-attributes endpoint, and every seeded conversation lost `demo_seed`
  -- defeating purge, backdate, the metrics exclusion flag and the Cases list
  simultaneously;
- `custom_attributes` carried no `case_category`/`case_subcategory`/
  `vehicle_model`/`vehicle_no`, all of which `mapping.py` reads off the
  CONVERSATION (it has no `meta.sender` to join through), so those warehouse
  columns were NULL for every demo row;
- the division label was slugified from the deck's display name
  ("after_sales") instead of the warehouse's canonical one ("aftersales"),
  splitting one division across two report buckets.
"""

from __future__ import annotations

from datetime import datetime, timezone

from client import build_case_custom_attributes, build_case_labels, conversation_source_id
from generator import DemoCase, DemoContact

BATCH = "seed-2026-08-04-a"
NOW = datetime(2026, 8, 4, 12, 0, 0, tzinfo=timezone.utc)

# The prefixes backend/.../metrics/mapping.py's _CHANNEL_BY_PREFIX recognises.
_MAPPED_CHANNEL_PREFIXES = {"whatsapp", "email", "phone", "sim", "zendesk", "chatwoot"}


def _contact() -> DemoContact:
    return DemoContact(
        name="[DEMO] Ahmad bin Abdullah",
        phone="+999123456789",
        email="ahmad.demo1@example.invalid",
        vehicle_no="W 1234",
        vehicle_model="e.MAS 7",
        purchased_from="Petaling Jaya",
    )


def _case(**overrides) -> DemoCase:
    defaults = dict(
        contact_index=0,
        channel="whatsapp",
        case_type="Inquiry",
        division="After Sales",
        concern="Spare Part",
        status="open",
        created_at=NOW,
        dealer=None,
        messages=[("customer", "hi")],
    )
    defaults.update(overrides)
    return DemoCase(**defaults)


# --- source_id -------------------------------------------------------------


def test_source_id_leads_with_the_channel_token_the_warehouse_parses():
    # channel_from_external_id does external_id.split("-", 1)[0], so the
    # channel has to be the FIRST hyphen-delimited token or every row is
    # "Other".
    source_id = conversation_source_id("whatsapp", BATCH, 7)
    assert source_id.split("-", 1)[0] == "whatsapp"


def test_source_id_channel_tokens_match_the_warehouses_prefix_table():
    # "social" is deliberately absent from the table: the warehouse has no
    # social bucket, and reporting it as "Other" is what it would do with a
    # real social conversation too.
    for channel in ("whatsapp", "phone", "email"):
        assert conversation_source_id(channel, BATCH, 0).split("-", 1)[0] in _MAPPED_CHANNEL_PREFIXES


def test_source_id_is_deterministic_in_batch_and_case_index():
    assert conversation_source_id("phone", BATCH, 3) == conversation_source_id("phone", BATCH, 3)
    assert conversation_source_id("phone", BATCH, 3) != conversation_source_id("phone", BATCH, 4)
    assert conversation_source_id("phone", BATCH, 3) != conversation_source_id("phone", "other", 3)


def test_source_id_stays_obviously_synthetic():
    # It must remain impossible to mistake for a real conversation's source_id.
    assert "demo-seed" in conversation_source_id("whatsapp", BATCH, 0)
    assert BATCH in conversation_source_id("whatsapp", BATCH, 0)


# --- custom_attributes -----------------------------------------------------


def test_purge_marker_is_always_present():
    attrs = build_case_custom_attributes(_case(), _contact(), BATCH, NOW)
    assert attrs["demo_seed"] == BATCH


def test_dealer_escalated_at_is_prewritten_when_the_case_has_a_dealer():
    # This is what stops maybe_stamp_dealer_escalation (woken by the labels
    # POST) from REPLACING the whole custom_attributes object with just its
    # own key: it short-circuits on `if existing.get("dealer_escalated_at")`.
    attrs = build_case_custom_attributes(_case(dealer="Shah Alam"), _contact(), BATCH, NOW)
    assert attrs["dealer_escalated_at"] == NOW.isoformat()
    assert attrs["dealer"] == "Shah Alam"


def test_a_case_with_no_dealer_gets_no_dealer_keys_at_all():
    # A dealer-less case gets no dealer_<slug> label, so the handler never
    # fires for it -- and inventing a timestamp would put a case that was
    # never escalated into the dealer-TAT view.
    attrs = build_case_custom_attributes(_case(dealer=None), _contact(), BATCH, NOW)
    assert "dealer_escalated_at" not in attrs
    assert "dealer" not in attrs


def test_case_category_is_the_canonical_division_not_the_display_one():
    attrs = build_case_custom_attributes(_case(division="After Sales"), _contact(), BATCH, NOW)
    assert attrs["case_category"] == "Aftersales"
    # The display vocabulary is still carried for the Cases list.
    assert attrs["division"] == "After Sales"


def test_case_subcategory_uses_the_flattened_label_subcategory_shape():
    # Same shape agent/app/services/categorize.py writes for real
    # conversations ("<Label>: <Subcategory>").
    attrs = build_case_custom_attributes(_case(division="Sales", concern="Booking"), _contact(), BATCH, NOW)
    assert attrs["case_subcategory"] == "Sales: Booking"


def test_vehicle_fields_are_copied_onto_the_conversation():
    # Spec §3 puts vehicle_no on both the contact and its conversations, and
    # mapping.py reads vehicle_model off the CONVERSATION -- the warehouse has
    # no meta.sender to join through the way the Cases list does.
    attrs = build_case_custom_attributes(_case(), _contact(), BATCH, NOW)
    assert attrs["vehicle_no"] == "W 1234"
    assert attrs["vehicle_model"] == "e.MAS 7"


def test_every_key_the_downstream_readers_need_is_present_in_one_object():
    # The endpoint REPLACES rather than merges, so there is exactly one
    # chance to write all of these.
    attrs = build_case_custom_attributes(_case(dealer="Ipoh"), _contact(), BATCH, NOW)
    required = {
        "demo_seed", "case_type", "division", "concern", "channel",
        "case_category", "case_subcategory", "vehicle_no", "vehicle_model",
        "dealer", "dealer_escalated_at",
    }
    assert required <= set(attrs)


# --- labels ----------------------------------------------------------------


def test_division_label_uses_the_canonical_slug_the_live_writer_emits():
    # backend/.../adapters/chatwoot.py::_classification_labels emits
    # division_<canonical.lower().replace(" ", "_")>, and mapping.py reads
    # that suffix back RAW. "division_after_sales" would be a second bucket.
    assert build_case_labels(_case(division="After Sales")) == ["division_aftersales"]


def test_dealer_label_only_appears_for_an_escalated_case():
    assert build_case_labels(_case(dealer=None)) == ["division_aftersales"]
    assert build_case_labels(_case(dealer="Kota Kinabalu")) == [
        "division_aftersales",
        "dealer_kota_kinabalu",
    ]
