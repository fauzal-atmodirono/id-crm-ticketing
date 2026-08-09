"""QA accuracy/quality label: the value object, the port, and the NoOp adapter.

P8 task 7 additions (call QA rubric):

- ``channel`` on ``QaLabel`` -- an optional dimension so a label can say
  which channel it reviews (today's channel-agnostic accuracy/quality label
  still works with it left ``None``; see ``call_qa_enabled`` in
  ``platform.config`` for why this is gated at the admin surface, not here).
- ``CallQaRubric`` -- the five-criterion manual call-QA rubric (greeting,
  identification, resolution, closing, compliance), scored as a percentage
  against P5's targets-store 85% value via ``percentage()``.

**This stays manual, on purpose.** The phone transcript path has never run
against a real Twilio call (see
``docs/testing/phone-channel-package-c-verification.md``), so an automatic
scorer built against it would be confident noise -- a number that LOOKS
like a measurement but was never validated against a real call. Nothing in
this module (or the admin surface built on it) computes a rubric score from
a transcript; a human reviewer scores each criterion, same shape as the
existing accuracy/quality label.

**A partially scored rubric is `incomplete`, never a low score.** A call
reviewed for `greeting` and `resolution` but not yet for the other three
criteria is a review IN PROGRESS, not a call that failed the three
unscored criteria -- treating an unscored criterion as a fail would report
a QA score for work nobody has actually judged yet, which the plan's own
"a per-agent score without a sample size is how a measurement becomes a
grievance" caution applies to just as much as a small sample does.
``percentage()`` returns ``None`` (not a number) until every criterion has
an explicit True/False, so a caller can never mistake "not yet reviewed"
for "reviewed and failed".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

# Order is the rubric's own, and is what every consumer (the admin surface,
# the BigQuery adapter, the percentage calculation below) iterates in.
RUBRIC_CRITERIA: tuple[str, ...] = (
    "greeting",
    "identification",
    "resolution",
    "closing",
    "compliance",
)


@dataclass(frozen=True)
class CallQaRubric:
    """The five-criterion manual call-QA rubric (P8 task 7).

    Each criterion is Pass (``True``), Fail (``False``), or not yet scored
    (``None``) -- three states, not two, because "not yet scored" and
    "failed" must never collapse into each other. See the module docstring
    for why an incomplete rubric reports `incomplete` rather than a score.
    """

    greeting: bool | None = None
    identification: bool | None = None
    resolution: bool | None = None
    closing: bool | None = None
    compliance: bool | None = None

    def _values(self) -> tuple[bool | None, ...]:
        return tuple(getattr(self, criterion) for criterion in RUBRIC_CRITERIA)

    def is_complete(self) -> bool:
        """True once every one of the five criteria has an explicit
        True/False -- i.e. the review is actually finished."""
        return all(v is not None for v in self._values())

    def percentage(self) -> float | None:
        """The rubric's score as a percentage (0-100) of criteria passed,
        or ``None`` when the rubric is only PARTIALLY scored. A half-filled
        form is `incomplete`, never a failing score -- see the module
        docstring."""
        if not self.is_complete():
            return None
        passed = sum(1 for v in self._values() if v)
        return passed / len(RUBRIC_CRITERIA) * 100.0


@dataclass(frozen=True)
class QaLabel:
    conversation_id: str
    accuracy: int
    quality: int
    reviewer: str
    notes: str
    labeled_at: datetime
    # P8 task 7: optional so every pre-existing channel-agnostic QaLabel
    # construction (and every row already in BigQuery without these
    # columns) keeps loading unchanged -- see call_qa_enabled's docstring
    # in platform.config for why these are gated at the admin surface.
    channel: str | None = None
    call_rubric: CallQaRubric | None = None


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
