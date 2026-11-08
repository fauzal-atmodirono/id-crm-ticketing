"""Unit tests for the PII masking rules (Wave 2 Task 29, FIX-49).

The false-positive cases matter as much as the hits: a report or a case page
full of bullets where dates, case numbers and prices used to be is a broken
page, not a compliant one.
"""

from __future__ import annotations

import pytest

from chatbot.features.privacy.pii import (
    DEFAULT_RULES,
    MASK,
    contains_mask,
    mask_pii,
    mask_pii_text,
)


@pytest.mark.parametrize(
    ("raw", "masked"),
    [
        ("900101-14-5678", "******-**-5678"),
        ("900101145678", "******-**-5678"),
        ("IC: 851231-10-1234.", "IC: ******-**-1234."),
        ("IC900101145678", "IC******-**-5678"),
    ],
)
def test_ic_number_patterns(raw: str, masked: str) -> None:
    assert mask_pii_text(raw) == masked


def test_phone_email_vin_card_patterns() -> None:
    # Phone: country code (or the national 01x prefix) and the last 4 kept.
    assert mask_pii_text("+60123456789") == f"+60{MASK * 5}6789"
    assert mask_pii_text("call +60 12-345 6789 now") == f"call +60{MASK * 5}6789 now"
    assert mask_pii_text("012-345 6789") == f"01{MASK * 4}6789"
    # Two numbers side by side must not merge into one "too long" non-match.
    assert mask_pii_text("0123456789 0198765432") == f"01{MASK * 4}6789 01{MASK * 4}5432"
    assert mask_pii_text("tel:+60123456789") == f"tel:+60{MASK * 5}6789"
    # Review fix 4: bare international form, as WhatsApp delivers it.
    assert mask_pii_text("wa from 60123456789 today") == f"wa from 60{MASK * 5}6789 today"
    assert mask_pii_text("60198765432.") == f"60{MASK * 5}5432."
    assert mask_pii_text("ref 601234567890123") == "ref 601234567890123"  # too long: an id
    assert mask_pii_text("CASE-60123456789") == "CASE-60123456789"  # inside an id
    # Lower-case chassis is still a chassis.
    assert mask_pii_text("pl1bt3lnrgb012345") == f"{MASK * 13}2345"
    # E-mail: first character and the domain.
    assert mask_pii_text("ahmad.ali@gmail.com") == f"a{MASK * 3}@gmail.com"
    # VIN / chassis: 17 chars, last 4 kept.
    assert mask_pii_text("chassis PL1BT3LNRGB012345") == f"chassis {MASK * 13}2345"
    # Card: Luhn-valid only, last 4 kept.
    assert mask_pii_text("card 4111 1111 1111 1111") == f"card {MASK * 12}1111"
    assert mask_pii_text("4111 1111 1111 1112") == "4111 1111 1111 1112"  # fails Luhn
    # Malaysian passport.
    assert mask_pii_text("passport A12345678") == f"passport {MASK * 5}5678"


@pytest.mark.parametrize(
    "text",
    [
        # dates and timestamps
        "2026-11-09",
        "09/11/2026",
        "2026-11-09T10:00:00+08:00",
        "2026-11-09T10:00:00.000123456+08:00",
        "20261109",
        "ref 1700000000",
        # ids and links
        "550e8400-e29b-41d4-a716-446655440000",
        "https://crm.example.com/app/accounts/1/conversations/0123456789",
        # case and ticket numbers
        "Case #20261109-0042",
        "CASE-000123",
        "ticket 1234567",
        "#900101145678",
        "order 1234567890123",
        # prices and quantities
        "RM 1,250.00",
        "RM120000",
        "RM 4,500,000.00",
        "total_km 120",
        # 12 digits that are not a valid IC date
        "123456789012",
        # vehicle plates are not on the rule list
        "BMT 5518",
        "WXY1234A",
        # 17 letters, no digits: a word, not a VIN
        "ABCDEFGHJKLMNPRST",
    ],
)
def test_false_positives_survive(text: str) -> None:
    assert mask_pii_text(text) == text


def test_structured_values_are_masked_by_key() -> None:
    row = {
        "customer_phone": "60123456789",  # stored without + or 0
        "email": "siti@proton.com",
        "ic_number": "900101145678",
        "bank_account_no": "1234567890",
        "vehicle_chassis": "PL1BT3LNRGB012345",
        "passport_no": "X1234567",
        "vehicle_no": "BMT5518",
        "remarks": "customer IC 900101-14-5678, call 0123456789",
        "total_km": 12,
        "incident_date": "2026-11-01",
        "nested": [{"phone_number": "+60123456789"}],
    }
    out = mask_pii(row)
    assert out["customer_phone"] == f"60{MASK * 5}6789"
    assert out["email"] == f"s{MASK * 3}@proton.com"
    assert out["ic_number"] == "******-**-5678"
    assert out["bank_account_no"] == f"{MASK * 6}7890"
    assert out["vehicle_chassis"] == f"{MASK * 13}2345"
    assert out["passport_no"] == f"{MASK * 4}4567"
    assert out["vehicle_no"] == "BMT5518"
    assert out["remarks"] == f"customer IC ******-**-5678, call 01{MASK * 4}6789"
    assert out["total_km"] == 12
    assert out["incident_date"] == "2026-11-01"
    assert out["nested"][0]["phone_number"] == f"+60{MASK * 5}6789"
    # Input untouched: masking returns a copy.
    assert row["customer_phone"] == "60123456789"


def test_none_and_empty_pass_through() -> None:
    assert mask_pii(None) is None
    assert mask_pii({"phone_number": None, "email": ""}) == {"phone_number": None, "email": ""}


def test_key_rules_are_anchored() -> None:
    out = mask_pii(
        {
            "bank_name": "Maybank",
            "bank_account": "1234567890",
            "account_type": "savings",
            "telegram": "@siti",
            "hotel": "Hilton 0123",
            "passport_country": "MY",
            "chassis_no": "PL1BT3LNRGB012345",
            "mobile_no": "0123456789",
        }
    )
    assert out["bank_name"] == "Maybank"
    assert out["bank_account"] == f"{MASK * 6}7890"
    assert out["account_type"] == "savings"
    assert out["telegram"] == "@siti"
    assert out["hotel"] == "Hilton 0123"
    assert out["passport_country"] == "MY"
    assert out["chassis_no"] == f"{MASK * 13}2345"
    assert out["mobile_no"] == f"01{MASK * 4}6789"


def test_contains_mask_matches_real_mask_shapes_only() -> None:
    assert contains_mask(f"+60{MASK * 5}1234")
    assert contains_mask(f"s{MASK * 3}@gmail.com")
    assert contains_mask("IC ******-**-5678")
    assert contains_mask({"x": [f"{MASK * 13}2345"]})
    # bullets as ordinary punctuation
    assert not contains_mask("• towed\n• battery replaced")
    assert not contains_mask(f"done {MASK * 3} next")
    assert not contains_mask(f"+60{MASK}{MASK}1234")
    assert not contains_mask("+60123451234")
    assert not contains_mask(None)


def test_default_rule_list_is_documented() -> None:
    names = {r.name for r in DEFAULT_RULES}
    assert names == {"email", "mykad_ic", "card_luhn", "phone", "vin_chassis", "passport_my"}
