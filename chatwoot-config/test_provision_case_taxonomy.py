# chatwoot-config/test_provision_case_taxonomy.py
from provision_case_taxonomy import _category_options, _load_taxonomy, _subcategory_options

TAXONOMY_JSON = '{"sales": {"label": "Sales", "subcategories": ["Test Drive Booking", "Pricing Inquiry"]}}'


def test_load_taxonomy_parses_dict():
    assert _load_taxonomy(TAXONOMY_JSON) == {
        "sales": {"label": "Sales", "subcategories": ["Test Drive Booking", "Pricing Inquiry"]}
    }


def test_load_taxonomy_rejects_non_dict():
    import pytest

    with pytest.raises(ValueError):
        _load_taxonomy("[1, 2, 3]")


def test_category_options():
    assert _category_options(_load_taxonomy(TAXONOMY_JSON)) == ["Sales"]


def test_subcategory_options_are_flattened_with_label_prefix():
    assert _subcategory_options(_load_taxonomy(TAXONOMY_JSON)) == [
        "Sales: Test Drive Booking",
        "Sales: Pricing Inquiry",
    ]
