"""P3 task 1 — the case fields PRO-NET's report decks print.

One definition of each field, its type and its validator, read by both the
mapper and the entry panel. Two of these carry real consequences if they go in
loose:

* **Plate normalisation.** `WXY 1234`, `wxy1234` and `WXY-1234` are one car. If
  they land in the warehouse as three, the vehicle dimension is worthless and
  no amount of downstream cleaning recovers it.
* **`purchased_from_dealer` is a validated slug, never free text.** Free text
  fragments the dealer dimension within days, and the dealer dimension is what
  the escalation routing keys on.

`escalated_to` deliberately rejects `hq` until client question Q5 is answered.
A plausible wrong number on a headline slide is worse than a truthful gap.
"""

from __future__ import annotations

import pytest

from chatbot.features.chat.case_fields import (
    CASE_FIELDS,
    InvalidCaseField,
    validate,
)


class _DealerStore:
    def __init__(self, slugs: set[str] | None = None) -> None:
        self._slugs = slugs if slugs is not None else {"komang_motor"}

    async def get(self, dealer: str):
        if dealer.lower() not in self._slugs:
            return None

        class _R:
            pass

        return _R()


def test_every_field_in_the_spec_has_a_name_a_type_and_a_validator():
    assert CASE_FIELDS
    for name, spec in CASE_FIELDS.items():
        assert spec.name == name
        assert spec.type in ("string", "enum", "slug")
        assert callable(spec.normalise)


def test_a_plate_number_is_normalised_to_upper_case_without_spaces():
    for raw in ("WXY 1234", "wxy1234", "WXY-1234", " wxy 1234 "):
        assert validate("vehicle_plate", raw) == "WXY1234"


def test_a_chassis_number_is_normalised_to_upper_case():
    assert validate("vehicle_chassis", " pl1abc123 ") == "PL1ABC123"


def test_a_free_text_wip_field_accepts_any_string_within_the_length_cap():
    assert validate("wip_issue", "Waiting on the parts order") == (
        "Waiting on the parts order"
    )
    assert validate("wip_action_taken", "Called the dealer twice") is not None
    assert validate("wip_next_action", "Escalate to regional manager") is not None
    assert validate("delay_reason", "Parts on back-order from Malaysia") is not None


def test_an_over_long_value_is_rejected_rather_than_silently_truncated():
    """Truncation would put a half-sentence in front of the client and look
    like the agent wrote it."""
    with pytest.raises(InvalidCaseField):
        validate("wip_issue", "x" * 5000)


def test_a_blank_value_is_accepted_as_clearing_the_field():
    assert validate("wip_issue", "") is None
    assert validate("wip_issue", "   ") is None


def test_escalated_to_accepts_dealer_and_none_but_not_hq():
    """DELIBERATE, TEMPORARY: `hq` is rejected until client question Q5 tells
    us what an HQ escalation actually is. When Q5 is answered this test
    changes, and the reviewer knows exactly why it changed."""
    assert validate("escalated_to", "dealer") == "dealer"
    assert validate("escalated_to", "none") == "none"
    with pytest.raises(InvalidCaseField) as excinfo:
        validate("escalated_to", "hq")
    assert "Q5" in str(excinfo.value)


def test_an_unknown_field_name_is_rejected():
    with pytest.raises(InvalidCaseField):
        validate("not_a_field", "x")


async def test_a_purchased_from_dealer_slug_that_exists_is_accepted():
    from chatbot.features.chat.case_fields import validate_dealer_slug

    assert await validate_dealer_slug("komang_motor", _DealerStore()) == "komang_motor"


async def test_a_purchased_from_dealer_slug_is_normalised_before_lookup():
    from chatbot.features.chat.case_fields import validate_dealer_slug

    assert await validate_dealer_slug(" Komang_Motor ", _DealerStore()) == "komang_motor"


async def test_a_purchased_from_dealer_slug_that_does_not_exist_is_rejected():
    from chatbot.features.chat.case_fields import validate_dealer_slug

    with pytest.raises(InvalidCaseField):
        await validate_dealer_slug("no_such_dealer", _DealerStore())


async def test_the_rejection_names_the_unknown_slug_so_the_operator_can_fix_it():
    from chatbot.features.chat.case_fields import validate_dealer_slug

    with pytest.raises(InvalidCaseField) as excinfo:
        await validate_dealer_slug("no_such_dealer", _DealerStore())
    assert "no_such_dealer" in str(excinfo.value)


async def test_an_unreachable_dealer_store_accepts_the_slug_rather_than_blocking():
    """Fail-open: a Firestore hiccup must not stop an agent recording which
    dealer sold the car. The dimension tolerates one unvalidated slug far
    better than the agent giving up on the field."""
    from chatbot.features.chat.case_fields import validate_dealer_slug

    class _Broken:
        async def get(self, dealer):
            raise RuntimeError("firestore down")

    assert await validate_dealer_slug("komang_motor", _Broken()) == "komang_motor"


async def test_no_dealer_store_configured_accepts_the_normalised_slug():
    from chatbot.features.chat.case_fields import validate_dealer_slug

    assert await validate_dealer_slug("Komang_Motor", None) == "komang_motor"
