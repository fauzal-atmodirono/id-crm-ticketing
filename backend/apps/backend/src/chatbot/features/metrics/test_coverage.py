"""P3 task 4 — say how sparse a sparse field is.

A view built on agent-entered data has to disclose its coverage, or the reader
draws a conclusion about the fleet from a chart that is really about which
agents filled the field in.
"""

from __future__ import annotations

from chatbot.features.metrics.coverage import (
    CoverageNote,
    coverage_note,
    coverage_pct,
    is_populated,
)


def _rows(*values):
    return [{"vehicle_plate": v} for v in values]


def test_three_of_five_populated_reports_sixty_percent():
    assert coverage_pct(_rows("A", "B", "C", None, None), "vehicle_plate") == 60.0


def test_an_empty_row_set_reports_none_not_zero():
    """0% coverage and "no cases at all" are different statements, and a slide
    printing "0% have a plate number" for an empty period is wrong."""
    assert coverage_pct([], "vehicle_plate") is None


def test_empty_strings_count_as_unpopulated():
    assert coverage_pct(_rows("A", ""), "vehicle_plate") == 50.0


def test_whitespace_only_values_count_as_unpopulated():
    assert coverage_pct(_rows("A", "   "), "vehicle_plate") == 50.0
    assert is_populated("  ") is False


def test_a_missing_attribute_counts_as_unpopulated():
    assert coverage_pct([{}, {"vehicle_plate": "A"}], "vehicle_plate") == 50.0


def test_dataclass_rows_work_as_well_as_dicts():
    class _Row:
        def __init__(self, plate):
            self.vehicle_plate = plate

    assert coverage_pct([_Row("A"), _Row(None)], "vehicle_plate") == 50.0


def test_the_note_names_the_field_so_the_slide_can_caption_it():
    note = coverage_note(_rows("A", "B", None, None), "vehicle_plate")
    assert isinstance(note, CoverageNote)
    assert note.field == "vehicle_plate"
    assert note.populated == 2
    assert note.total == 4
    assert "vehicle_plate" in note.caption
    assert "50%" in note.caption


def test_the_empty_caption_does_not_claim_zero_percent():
    caption = coverage_note([], "vehicle_plate").caption
    assert "0%" not in caption
    assert "No cases" in caption
