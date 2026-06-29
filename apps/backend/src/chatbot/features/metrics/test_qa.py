from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from chatbot.features.metrics.qa import NoOpQaLabels, QaLabel


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
