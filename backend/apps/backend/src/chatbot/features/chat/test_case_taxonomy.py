from chatbot.features.chat.case_taxonomy import build_case_taxonomy
from chatbot.platform.config import Settings


def _settings(taxonomy_json: str) -> Settings:
    return Settings(_env_file=None, case_taxonomy_json=taxonomy_json)


VALID = """
{
  "sales": {"label": "Sales", "subcategories": ["Test Drive Booking", "Pricing Inquiry"]},
  "apps": {"label": "Apps", "subcategories": ["Login Issue"]}
}
"""


def test_valid_taxonomy_lookups():
    tax = build_case_taxonomy(_settings(VALID))
    assert tax.main_categories() == ["sales", "apps"]
    assert tax.label_for("sales") == "Sales"
    assert tax.label_for("SALES") == "Sales"  # case-insensitive
    assert tax.subcategories_for("sales") == ["Test Drive Booking", "Pricing Inquiry"]
    assert tax.is_valid_category("sales") is True
    assert tax.is_valid_category("unknown") is False
    assert tax.is_valid_subcategory("sales", "Pricing Inquiry") is True
    assert tax.is_valid_subcategory("sales", "Not A Real Sub") is False
    assert tax.is_empty() is False


def test_flattened_subcategory_options():
    tax = build_case_taxonomy(_settings(VALID))
    assert tax.flattened_subcategory_options() == [
        "Sales: Test Drive Booking",
        "Sales: Pricing Inquiry",
        "Apps: Login Issue",
    ]


def test_empty_json_yields_empty_taxonomy():
    tax = build_case_taxonomy(_settings(""))
    assert tax.is_empty() is True
    assert tax.main_categories() == []
    assert tax.is_valid_category("sales") is False


def test_malformed_json_yields_empty_taxonomy_not_crash():
    tax = build_case_taxonomy(_settings("{not valid json"))
    assert tax.is_empty() is True


def test_non_dict_json_yields_empty_taxonomy():
    tax = build_case_taxonomy(_settings("[1, 2, 3]"))
    assert tax.is_empty() is True


def test_entry_missing_label_is_skipped_not_crash():
    tax = build_case_taxonomy(_settings('{"sales": {"subcategories": ["x"]}}'))
    assert tax.is_empty() is True


def test_subcategories_default_to_empty_list_when_absent():
    tax = build_case_taxonomy(_settings('{"sales": {"label": "Sales"}}'))
    assert tax.subcategories_for("sales") == []


def test_subcategories_wrong_type_ignored_not_crash():
    tax = build_case_taxonomy(_settings('{"sales": {"label": "Sales", "subcategories": "not-a-list"}}'))
    assert tax.subcategories_for("sales") == []


def test_default_settings_produce_non_empty_taxonomy():
    # No case_taxonomy_json override — exercises the actual shipped default
    # value in config.py end-to-end, so a typo in that hand-written JSON
    # string fails this test instead of silently degrading to an empty
    # taxonomy (fail-open) with only a warning-level log.
    tax = build_case_taxonomy(Settings(_env_file=None))
    assert tax.is_empty() is False
    assert tax.main_categories() == [
        "sales",
        "aftersales",
        "apps",
        "charging",
        "product",
        "marketing",
        "others",
    ]
