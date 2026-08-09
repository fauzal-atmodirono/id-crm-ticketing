from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from chatbot.features.metrics.attainment import evaluate
from chatbot.features.metrics.qa import CallQaRubric, NoOpQaLabels, QaLabel
from chatbot.features.metrics.qa_schema import qa_view_ddls
from chatbot.features.metrics.targets_store import Target


def test_qa_label_is_frozen_with_expected_fields() -> None:
    ts = datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)
    label = QaLabel(
        conversation_id="T-1",
        accuracy=88,
        quality=92,
        reviewer="alice",
        notes="missed one spec detail",
        labeled_at=ts,
    )
    assert label.conversation_id == "T-1"
    assert label.accuracy == 88
    assert label.quality == 92
    assert label.reviewer == "alice"
    assert label.notes == "missed one spec detail"
    assert label.labeled_at == ts
    with pytest.raises(FrozenInstanceError):
        label.accuracy = 0  # type: ignore[misc]


async def test_noop_qa_labels_record_is_a_noop() -> None:
    label = QaLabel(
        conversation_id="T-1",
        accuracy=1,
        quality=2,
        reviewer="bob",
        notes="",
        labeled_at=datetime.now(UTC),
    )
    # Must not raise and must return None.
    assert await NoOpQaLabels().record_label(label) is None  # type: ignore[func-returns-value]


# --- P8 task 7: channel dimension + the five-criterion call rubric ---------


def test_a_qa_record_carries_its_channel() -> None:
    label = QaLabel(
        conversation_id="T-9",
        accuracy=90,
        quality=90,
        reviewer="alice",
        notes="",
        labeled_at=datetime.now(UTC),
        channel="Phone",
    )
    assert label.channel == "Phone"


def test_existing_channel_agnostic_qa_records_still_load() -> None:
    """A label built the pre-P8 way (no channel, no rubric kwargs at all)
    must keep constructing and reading exactly as before -- the two new
    fields are additive, defaulted, and never required."""
    label = QaLabel(
        conversation_id="T-1",
        accuracy=88,
        quality=92,
        reviewer="alice",
        notes="missed one spec detail",
        labeled_at=datetime(2026, 6, 29, tzinfo=UTC),
    )
    assert label.channel is None
    assert label.call_rubric is None


def test_the_call_rubric_scores_five_criteria_as_a_percentage() -> None:
    rubric = CallQaRubric(
        greeting=True,
        identification=True,
        resolution=True,
        closing=False,
        compliance=True,
    )
    assert rubric.is_complete() is True
    assert rubric.percentage() == pytest.approx(80.0)  # 4 of 5 passed


def test_a_partially_scored_rubric_reports_incomplete_rather_than_a_low_score() -> None:
    """A half-filled rubric -- greeting/resolution scored, the rest not yet
    reviewed -- is a review IN PROGRESS. It must report `incomplete`
    (percentage() -> None), never be silently treated as three failing
    criteria, which would score a call nobody has finished judging."""
    rubric = CallQaRubric(greeting=True, resolution=False)
    assert rubric.is_complete() is False
    assert rubric.percentage() is None

    # Even all-Fail-so-far-but-incomplete must not read as a real 0%.
    rubric_all_fail_so_far = CallQaRubric(greeting=False, identification=False)
    assert rubric_all_fail_so_far.percentage() is None


def test_the_call_qa_percentage_compares_against_the_targets_store_value() -> None:
    """The rubric's percentage composes with the SAME attainment machinery
    every other rate metric in this package uses -- P5's targets store and
    `attainment.evaluate`, not a bespoke comparison."""
    target = Target(key="call_qa", comparator="gte", value=85, unit="percent")

    below_target = CallQaRubric(
        greeting=True, identification=True, resolution=False, closing=False, compliance=True
    )
    assert below_target.percentage() == pytest.approx(60.0)
    below = evaluate(below_target.percentage(), target)
    assert below.status == "missed"

    at_target = CallQaRubric(
        greeting=True, identification=True, resolution=True, closing=True, compliance=False
    )
    assert at_target.percentage() == pytest.approx(80.0)
    still_missed = evaluate(at_target.percentage(), target)
    assert still_missed.status == "missed"

    perfect = CallQaRubric(
        greeting=True, identification=True, resolution=True, closing=True, compliance=True
    )
    assert perfect.percentage() == pytest.approx(100.0)
    met = evaluate(perfect.percentage(), target)
    assert met.status == "met"

    # An incomplete rubric has nothing to compare -- `no_data`, not `missed`.
    incomplete = evaluate(CallQaRubric(greeting=True).percentage(), target)
    assert incomplete.status == "no_data"


def test_v_quality_is_unchanged_for_existing_consumers() -> None:
    """The exact pre-P8 v_quality SQL, byte-for-byte -- adding the new call-QA
    columns/view must not touch the view existing dashboards already read."""
    ddls = qa_view_ddls("proj", "ds", "qa_labels", "conversations")
    assert ddls["v_quality"] == (
        "CREATE OR REPLACE VIEW `proj.ds.v_quality` AS "
        "SELECT c.channel, "
        "COUNT(*) AS labels, "
        "AVG(q.accuracy) AS avg_accuracy, "
        "AVG(q.quality) AS avg_quality "
        "FROM `proj.ds.qa_labels` q "
        "JOIN `proj.ds.conversations` c USING (conversation_id) "
        "GROUP BY c.channel"
    )
