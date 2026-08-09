"""The provisioning script and the validator must define the same field set.

If they drift, a field is either invisible in the sidebar (defined in the
backend, never provisioned) or reaches the warehouse unvalidated (provisioned,
never defined). Both failures are silent, which is why this is a test.
"""

from __future__ import annotations

import provision_case_record_fields as prov

# Owned by provision_case_taxonomy.py; defining them in both scripts would
# fight over the option lists.
TAXONOMY_OWNED = {"case_detail", "case_state"}


def test_every_provisioned_field_is_validated_by_the_backend():
    from pathlib import Path

    case_fields = (
        Path(__file__).resolve().parents[1]
        / "backend/apps/backend/src/chatbot/features/chat/case_fields.py"
    ).read_text(encoding="utf-8")

    for key, _name, _type, _options in prov.FIELDS:
        assert f'"{key}"' in case_fields, (
            f"{key} is provisioned but has no validator in case_fields.py"
        )


def test_the_taxonomy_owned_fields_are_not_redefined_here():
    keys = {key for key, *_ in prov.FIELDS}
    assert not (keys & TAXONOMY_OWNED), (
        "provision_case_taxonomy.py already owns these; two scripts writing the "
        "same definition will fight over its option list"
    )


def test_escalated_to_offers_no_hq_option():
    """Q5 is unanswered: offering the option produces a number nobody can
    defend. When Q5 is answered this test changes, deliberately."""
    options = next(o for k, _n, _t, o in prov.FIELDS if k == "escalated_to")
    assert options == ["dealer", "none"]


def test_only_the_enum_field_declares_options():
    for key, _name, display_type, options in prov.FIELDS:
        if display_type == "list":
            assert options, f"{key} is a list with no options"
        else:
            assert options == [], f"{key} is free text but declares options"
