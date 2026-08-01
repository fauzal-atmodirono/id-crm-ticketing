from app.config import get_settings
from app.services.case_taxonomy import build_case_taxonomy


def _settings(taxonomy_json: str):
    s = get_settings()
    return s.model_copy(update={"case_taxonomy_json": taxonomy_json})


VALID = '{"sales": {"label": "Sales", "subcategories": ["Test Drive Booking"]}}'


def test_valid_taxonomy_lookups():
    tax = build_case_taxonomy(_settings(VALID))
    assert tax.main_categories() == ["sales"]
    assert tax.label_for("sales") == "Sales"
    assert tax.subcategories_for("sales") == ["Test Drive Booking"]
    assert tax.is_valid_category("sales") is True


def test_empty_json_yields_empty_taxonomy():
    tax = build_case_taxonomy(_settings(""))
    assert tax.is_empty() is True


def test_malformed_json_yields_empty_taxonomy_not_crash():
    tax = build_case_taxonomy(_settings("{broken"))
    assert tax.is_empty() is True
