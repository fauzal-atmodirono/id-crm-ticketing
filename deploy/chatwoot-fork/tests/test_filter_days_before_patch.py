"""Fork patch 0086 (Wave 2 task 30, FIX-51): conversations/filter with
filter_operator days_before returned HTTP 500 because FilterService called
`with_indifferent_access` on an ActionController::Parameters (NoMethodError).
Content-level checks; the chain-apply is run by the SDD task."""
from pathlib import Path

PATCH = Path(__file__).resolve().parent.parent / "patches" / "0086-filter-days-before-params.patch"
LINES = PATCH.read_text().split("\n")
ADDED = "\n".join(l[1:] for l in LINES if l.startswith("+") and not l.startswith("+++"))
REMOVED = "\n".join(l[1:] for l in LINES if l.startswith("-") and not l.startswith("---"))


def test_touches_only_filter_service():
    files = [l for l in LINES if l.startswith("diff --git ")]
    assert files == ["diff --git a/app/services/filter_service.rb b/app/services/filter_service.rb"]


def test_params_are_converted_before_with_indifferent_access():
    assert "to_unsafe_h" in ADDED
    assert "query_hash.with_indifferent_access" in REMOVED
    assert "query_hash.with_indifferent_access" not in ADDED


def test_plain_hashes_still_supported():
    assert "respond_to?(:to_unsafe_h)" in ADDED
