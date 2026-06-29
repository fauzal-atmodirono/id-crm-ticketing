"""QA accuracy/quality label: the value object, the port, and the NoOp adapter."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class QaLabel:
    conversation_id: str
    accuracy: int
    quality: int
    reviewer: str
    notes: str
    labeled_at: datetime


class QaLabelPort(Protocol):
    """Port for recording one manual QA label per reviewed conversation."""

    async def record_label(self, label: QaLabel) -> None:
        """Best-effort: persist a single QA label. Must never raise."""
        ...


class NoOpQaLabels:
    """Default QaLabelPort — drops every label. Used in dev/tests and when
    qa_provider != 'bigquery'."""

    async def record_label(self, _label: QaLabel) -> None:
        return None
