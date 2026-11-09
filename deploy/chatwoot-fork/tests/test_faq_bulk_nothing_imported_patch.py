"""Fork patch 0087 (Wave 2 task 30, FIX-53): the FAQ upload page shows
'Nothing imported' and offers 'Import valid rows only' (?partial=true)."""
from pathlib import Path

PATCH = Path(__file__).resolve().parent.parent / "patches" / "0087-faq-bulk-nothing-imported.patch"
ADDED = "\n".join(l[1:] for l in PATCH.read_text().split("\n") if l.startswith("+") and not l.startswith("+++"))


def test_nothing_imported_message_and_action():
    assert "Nothing imported — fix the errors or import valid rows only" in ADDED
    assert "Import valid rows only" in ADDED
    assert "result.imported === false" in ADDED


def test_partial_query_param_is_sent_only_on_request():
    assert "partial ? '?partial=true' : ''" in ADDED
    assert "{ partial: true }" in ADDED
