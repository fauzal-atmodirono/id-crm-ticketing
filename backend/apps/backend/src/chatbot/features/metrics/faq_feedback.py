"""FAQ feedback: value object, port, and NoOp adapter."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class FaqFeedback:
    article_id: str
    session_id: str
    helpful: bool
    score: int
    at: datetime


class FaqFeedbackPort(Protocol):
    async def record_feedback(self, fb: FaqFeedback) -> None:
        """Best-effort: persist one FAQ-feedback row. Must never raise."""
        ...


class NoOpFaqFeedback:
    async def record_feedback(self, _fb: FaqFeedback) -> None:
        return None
